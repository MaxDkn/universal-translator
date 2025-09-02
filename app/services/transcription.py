import json
import queue
import asyncio
import threading
import requests
import logging
from websockets.exceptions import ConnectionClosedOK
from websockets.asyncio.client import ClientConnection, connect

from ..core.enums import InitiateResponse, StreamingConfiguration
from ..audio.io import AudioCapture
from .tts import TTSService

logger = logging.getLogger(__name__)


def is_gladia_key_valid(key: str | None, url: str = "https://api.gladia.io/v2/pre-recorded") -> str:
    """Checks if the Gladia key is valid."""
    from ..core.exceptions import InvalidGladiaKeyException
    
    if not key:
        raise InvalidGladiaKeyException("No Gladia API key provided and 'GLADIA_KEY' environment variable not set.")
    
    response = requests.request("GET", url, headers={"x-gladia-key": key})
    if not response.ok:
        raise InvalidGladiaKeyException()
    
    return key


class TranscriptionAndVoiceService:
    """Service for real-time transcription and translation."""
    
    def __init__(self, gladia_key: str, audio_capture: AudioCapture, 
                 tts_service: TTSService, agent_language: str = "fr", 
                 target_language: str = "en"):
        self.audio_capture = audio_capture
        self.gladia_key = gladia_key
        self.tts_service = tts_service
        self.agent_language = agent_language
        self.target_language = target_language
        
        self.is_traduction = (
            self.target_language and 
            self.target_language != "auto" and 
            agent_language != target_language
        )
        
        self.is_transcribing = False
        self.listener_queue = None
        self.stop_event = threading.Event()
        
        self.STREAMING_CONFIGURATION: StreamingConfiguration = {
            "encoding": "wav/pcm",
            "sample_rate": self.audio_capture.SAMPLE_RATE,
            "bit_depth": 16,
            "channels": 1,
            "language_config": {
                "languages": [],
                "code_switching": True,
            },
        }
        
        if self.is_traduction:
            self.STREAMING_CONFIGURATION["realtime_processing"] = {
                "translation": True,
                "translation_config": {
                    "target_languages": [target_language],
                    "model": "base",
                    "match_original_utterances": True,
                    "lipsync": True,
                    "context_adaptation": True,
                    "context": "This is a conversation in a Call center for Gladia product",
                    "informal": False
                }
            }
            logger.info(f"Translation enabled - target language: {self.target_language}")

    def init_live_session(self) -> InitiateResponse:
        """Initialize a live transcription session."""
        response = requests.post(
            "https://api.gladia.io/v2/live",
            headers={"X-Gladia-Key": self.gladia_key},
            json=self.STREAMING_CONFIGURATION,
            timeout=10,
        )
        
        if not response.ok:
            logger.error(f"{response.status_code}: {response.text or response.reason}")
            exit(response.status_code)
            
        return response.json()

    async def print_messages_from_socket(self, socket: ClientConnection) -> None:
        """Handle incoming messages from WebSocket."""
        async for message in socket:
            if self.stop_event.is_set():
                break
                
            content = json.loads(message)
            
            if self.is_transcribing and content["type"] == "translation":
                text = content["data"]["translated_utterance"]["text"].strip()
                logger.translate(f"[{self.audio_capture.device.name}]: {text}")
                if self.tts_service and text:
                    asyncio.create_task(self.tts_service.synthesize_and_queue(text))
                    
            elif content["type"] == "transcription":
                text = content["data"]["translated_utterance"]["text"].strip()
                logger.translate(" " * 4, text)
                
            if content["type"] == "post_final_transcript":
                logger.debug("Transcription finished automatically")
                self.is_transcribing = False
                break

    async def send_prebuffer_chunks(self, socket: ClientConnection) -> None:
        """Send pre-buffer chunks at the beginning of transcription."""
        prebuffer_chunks = self.audio_capture.buffer.get_prebuffer_chunks()
        if prebuffer_chunks:
            logger.debug(f"Sending {len(prebuffer_chunks)} pre-buffer chunks "
                        f"({self.audio_capture.buffer.prebuffer_seconds}s)")
            for chunk_info in prebuffer_chunks:
                if self.stop_event.is_set():
                    break
                json_data = json.dumps({
                    "type": "audio_chunk",
                    "data": {"chunk": chunk_info['base64']}
                })
                await socket.send(json_data)
                await asyncio.sleep(0.005)

    async def send_audio_from_buffer(self, socket: ClientConnection) -> None:
        """Send audio chunks from buffer to WebSocket."""
        while self.is_transcribing and not self.stop_event.is_set():
            try:
                if self.listener_queue is None:
                    await asyncio.sleep(0.01)
                    continue
                    
                chunk_info = self.listener_queue.get(timeout=0.1)
                json_data = json.dumps({
                    "type": "audio_chunk",
                    "data": {"chunk": chunk_info['base64']}
                })
                await socket.send(json_data)
                await asyncio.sleep(0.01)
            except queue.Empty:
                continue
            except ConnectionClosedOK:
                break

    async def stop_recording(self, websocket: ClientConnection) -> None:
        """Send stop recording command to WebSocket."""
        await websocket.send(json.dumps({"type": "stop_recording"}))
        await asyncio.sleep(0)

    async def run_transcription(self):
        """Run the complete transcription process."""
        if self.is_transcribing:
            return
            
        self.is_transcribing = True
        self.stop_event.clear()
        self.listener_queue = self.audio_capture.buffer.register_listener()
        
        try:
            response = self.init_live_session()
            async with connect(response["url"]) as websocket:
                logger.debug("Transcription started with pre-buffer")
                
                await self.send_prebuffer_chunks(websocket)
                self.audio_capture.buffer.start_recording()
                
                send_audio_task = asyncio.create_task(self.send_audio_from_buffer(websocket))
                print_messages_task = asyncio.create_task(self.print_messages_from_socket(websocket))
                
                while self.is_transcribing and not self.stop_event.is_set():
                    done, pending = await asyncio.wait(
                        [send_audio_task, print_messages_task],
                        return_when=asyncio.FIRST_COMPLETED,
                        timeout=0.1
                    )
                    
                    if done:
                        for task in done:
                            if task.exception():
                                logger.error(f"Error in a task: {task.exception()}")
                                self.is_transcribing = False
                                break
                
                if self.is_transcribing:
                    await self.stop_recording(websocket)
                
                for task in [send_audio_task, print_messages_task]:
                    if not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                            
        except Exception as e:
            logger.error(f"Error during transcription: {e}")
        finally:
            self.is_transcribing = False
            self.audio_capture.buffer.stop_recording()
            self.audio_capture.buffer.unregister_listener(self.listener_queue)

    def stop_transcription(self):
        """Stop the transcription process."""
        logger.debug("Stopping transcription requested")
        self.stop_event.set()
        self.is_transcribing = False
