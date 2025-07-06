import os
import json
import time
import queue
import base64
import asyncio
import logging
import argparse
import threading
from enum import Enum
from collections import deque
from typing import Literal, TypedDict, List
from datetime import datetime, timedelta

import torch
import pyaudio
import requests
import edge_tts
import numpy as np
from websockets.exceptions import ConnectionClosedOK
from websockets.asyncio.client import ClientConnection, connect
from silero_vad import get_speech_timestamps, load_silero_vad


logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:     %(asctime)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


class InvalidGladiaKeyException(Exception):
    """Exception raised when the Gladia API key is invalid."""
    def __init__(self, message="The provided Gladia API key is invalid."):
        super().__init__(message)

class DeviceNotFoundException(Exception):
    """Exception raised when the specified USB device is not found."""
    def __init__(self, device: str):
        message = f"The device '{device}' was not found in the USB devices list."
        super().__init__(message)

class VADSetupException(Exception):
    """Exception raised when the real-time VAD setup fails."""
    def __init__(self, message="Failed to initialize real-time voice activity detection (VAD)."):
        super().__init__(message)

class AudioStreamStartException(Exception):
    """Exception raised when the audio input stream fails to start."""
    def __init__(self, device_name: str, reason: str = ""):
        message = f"Failed to start audio stream for device '{device_name}'. {reason}"
        super().__init__(message)


class TranscriptionState(Enum):
    IDLE = "idle"
    TRANSCRIBING = "transcribing"
    WAITING_SILENCE = "waiting_silence"

class InitiateResponse(TypedDict):
    id: str
    url: str

class LanguageConfiguration(TypedDict):
    languages: list[str] | None
    code_switching: bool | None

class StreamingConfiguration(TypedDict):
    encoding: Literal["wav/pcm", "wav/alaw", "wav/ulaw"]
    bit_depth: Literal[8, 16, 24, 32]
    sample_rate: Literal[8_000, 16_000, 32_000, 44_100, 48_000]
    channels: int
    language_config: LanguageConfiguration | None

class AudioDevice:
    def __init__(self, index: int, name: str, sample_rate: int, max_channels: int):
        self.index = index
        self.name = name
        self.sample_rate = sample_rate
        self.max_channels = max_channels

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"AudioDevice(name='{self.name}', index={self.index}, sample_rate={self.sample_rate}, max_channels={self.max_channels})"


def is_gladia_key_valid(key: str | None, url: str = "https://api.gladia.io/v2/pre-recorded") -> str:
    """
    Checks if the Gladia key is valid.

    Args:
        key (str): Gladia API key.
        url (str): URL used to check the key.

    Returns:
        str: The key if it is valid.

    Raises:
        InvalidGladiaKeyException: If the key is invalid.
    """
    if not key:
        raise InvalidGladiaKeyException("No Gladia API key provided and 'GLADIA_KEY' environment variable not set.")
    response = requests.request("GET", url, headers={"x-gladia-key": key})
    if not response.ok:
        raise InvalidGladiaKeyException()
    return key

def get_device_info(device_name: str) -> AudioDevice:
    """Find audio device info by USB device name with fallback options"""
    p = pyaudio.PyAudio()
    
    candidates = []
    
    for i in range(p.get_device_count()):
        try:
            device_info = p.get_device_info_by_index(i)
            if int(device_info['maxInputChannels']) > 0:  # Input device
                if device_name.lower() in str(device_info['name']).lower():
                    candidates.append(AudioDevice(
                        index=i,
                        name=str(device_info['name']),
                        sample_rate=int(device_info['defaultSampleRate']),
                        max_channels=int(device_info['maxInputChannels'])
                    ))
        except Exception:
            continue
    
    p.terminate()
    
    if not candidates:
        raise DeviceNotFoundException(device_name)
    
    return max(candidates, key=lambda x: x.sample_rate)


class SharedAudioBuffer:
    def __init__(self, max_chunks: int = 1000, prebuffer_seconds: float = 3.0):
        self.buffer = deque(maxlen=max_chunks)
        self.lock = threading.Lock()
        self.is_recording = False
        self.listeners = []

        # Configuration du pre-buffer
        self.prebuffer_seconds = prebuffer_seconds
        self.prebuffer_duration = timedelta(seconds=prebuffer_seconds)
        
    def start_recording(self):
        with self.lock:
            self.is_recording = True
            # Ne pas vider le buffer pour garder l'historique
            
    def stop_recording(self):
        with self.lock:
            self.is_recording = False
            
    def add_chunk(self, chunk_data: bytes):
        with self.lock:
            timestamp = datetime.now()
            chunk_info = {
                'data': chunk_data,
                'timestamp': timestamp,
                'base64': base64.b64encode(chunk_data).decode('utf-8')
            }
            self.buffer.append(chunk_info)
            
            # Notifier les listeners seulement si on est en train d'enregistrer
            if self.is_recording:
                for listener in self.listeners:
                    try:
                        listener.put_nowait(chunk_info)
                    except queue.Full:
                        pass

    def get_all_chunks(self) -> list:
        with self.lock:
            return list(self.buffer)
    
    def get_prebuffer_chunks(self) -> list:
        """Récupère les chunks du pre-buffer (dernières X secondes)"""
        with self.lock:
            if not self.buffer:
                return []
                
            current_time = datetime.now()
            cutoff_time = current_time - self.prebuffer_duration
            
            # Trouver les chunks dans la fenêtre de pre-buffer
            prebuffer_chunks = []
            for chunk_info in reversed(self.buffer):
                if chunk_info['timestamp'] >= cutoff_time:
                    prebuffer_chunks.append(chunk_info)
                else:
                    break
                    
            # Retourner dans l'ordre chronologique
            return list(reversed(prebuffer_chunks))
            
    def register_listener(self) -> queue.Queue:
        listener_queue = queue.Queue(maxsize=100)
        self.listeners.append(listener_queue)
        return listener_queue
        
    def unregister_listener(self, listener_queue: queue.Queue):
        if listener_queue in self.listeners:
            self.listeners.remove(listener_queue)

class AudioCapture:
    def __init__(self, buffer: SharedAudioBuffer):
        self.buffer = buffer
        self.p = pyaudio.PyAudio()
        self.stream = None
        self.is_running = False
        self.thread = None
        
        self.CHANNELS = 1
        self.FORMAT = pyaudio.paInt16
        self.FRAMES_PER_BUFFER = 3200
        self.SAMPLE_RATE = 16_000
        
    def start_capture(self):
        if self.is_running:
            return
            
        self.is_running = True
        
        self.stream = self.p.open(
            format=self.FORMAT,
            channels=self.CHANNELS,
            rate=self.SAMPLE_RATE,
            input=True,
            frames_per_buffer=self.FRAMES_PER_BUFFER,
        )
        
        self.thread = threading.Thread(target=self._capture_loop)
        self.thread.daemon = True
        self.thread.start()
        
    def stop_capture(self):
        self.is_running = False
        
        if self.thread:
            self.thread.join()
            
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            
    def _capture_loop(self):
        while self.is_running:
            try:
                if self.stream is None:
                    logger.error("Stream audio non initialisé")
                    break
                
                data = self.stream.read(self.FRAMES_PER_BUFFER, exception_on_overflow=False)
                # Ajouter TOUJOURS les chunks au buffer (même si pas en recording)
                self.buffer.add_chunk(data)
                time.sleep(0.01)
            except Exception as e:
                logger.error(f"Erreur capture audio: {e}")
                break
                
    def cleanup(self):
        self.stop_capture()
        self.p.terminate()

class TranscriptionService:
    def __init__(self, buffer: SharedAudioBuffer, gladia_key: str):
        self.buffer = buffer
        self.is_transcribing = False
        self.listener_queue = None
        self.stop_event = threading.Event()
        self.gladia_key = gladia_key
        
        self.STREAMING_CONFIGURATION: StreamingConfiguration = {
            "encoding": "wav/pcm",
            "sample_rate": 16_000,
            "bit_depth": 16,
            "channels": 1,
            "language_config": {
                "languages": [],
                "code_switching": True,
            },
        }
        
    def init_live_session(self) -> InitiateResponse:
        response = requests.post(
            "https://api.gladia.io/v2/live",
            headers={"X-Gladia-Key": self.gladia_key},
            json=self.STREAMING_CONFIGURATION,
            timeout=3,
        )
        if not response.ok:
            print(f"{response.status_code}: {response.text or response.reason}")
            exit(response.status_code)
        return response.json()
        
    async def print_messages_from_socket(self, socket: ClientConnection) -> None:
        async for message in socket:
            if self.stop_event.is_set():
                break
                
            content = json.loads(message)
            if content["type"] == "transcript" and content["data"]["is_final"]:
                text = content["data"]["utterance"]["text"].strip()
                print(" "*4, text)
            if content["type"] == "post_final_transcript":
                logger.debug("Transcription finished automatically")
                self.is_transcribing = False
                break
    
    async def send_prebuffer_chunks(self, socket: ClientConnection) -> None:
        """Envoie les chunks du pre-buffer au début de la transcription"""
        prebuffer_chunks = self.buffer.get_prebuffer_chunks()
        
        if prebuffer_chunks:
            logger.debug(f"Sending {len(prebuffer_chunks)} pre-buffer chunks ({self.buffer.prebuffer_seconds}s)")
            
            for chunk_info in prebuffer_chunks:
                if self.stop_event.is_set():
                    break
                    
                json_data = json.dumps({
                    "type": "audio_chunk", 
                    "data": {"chunk": chunk_info['base64']}
                })
                await socket.send(json_data)
                # Petit délai pour ne pas surcharger
                await asyncio.sleep(0.005)
                
    async def send_audio_from_buffer(self, socket: ClientConnection) -> None:
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
        await websocket.send(json.dumps({"type": "stop_recording"}))
        await asyncio.sleep(0)
        
    async def run_transcription(self):
        if self.is_transcribing:
            return
            
        self.is_transcribing = True
        self.stop_event.clear()
        self.listener_queue = self.buffer.register_listener()
        
        try:
            response = self.init_live_session()
            
            async with connect(response["url"]) as websocket:
                logger.debug("Transcription started with pre-buffer")
                
                # 1. D'abord envoyer le pre-buffer
                await self.send_prebuffer_chunks(websocket)
                
                # 2. Démarrer l'enregistrement pour les nouveaux chunks
                self.buffer.start_recording()
                
                # 3. Démarrer les tâches de streaming
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
                                print(f"Erreur dans une tâche: {task.exception()}")
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
            print(f"Erreur pendant la transcription: {e}")
        finally:
            self.is_transcribing = False
            self.buffer.stop_recording()
            self.buffer.unregister_listener(self.listener_queue)
            
    def stop_transcription(self):
        logger.debug("Stopping transcription requested")
        self.stop_event.set()
        self.is_transcribing = False

class VADTranscriptionController:
    def __init__(self, audio_capture: AudioCapture, transcription_service: TranscriptionService, vad_model, silence_timeout: float = 30.0):
        self.audio_capture = audio_capture
        self.transcription_service = transcription_service
        self.vad_model = vad_model
        self.silence_timeout = silence_timeout
        
        self.state = TranscriptionState.IDLE
        self.state_lock = threading.Lock()
        
        self.vad_thread = None
        self.silence_timer = None
        self.is_monitoring = False
        self.stop_monitoring_event = threading.Event()
        
        self.last_speech_time = None
        self.vad_check_interval = 0.5
        
    def start_monitoring(self):
        if self.is_monitoring:
            return
            
        self.is_monitoring = True
        self.stop_monitoring_event.clear()
        
        self.vad_thread = threading.Thread(target=self._vad_monitoring_loop)
        self.vad_thread.daemon = True
        self.vad_thread.start()
        
        logger.debug("VAD monitoring started")
        
    def stop_monitoring(self):
        if not self.is_monitoring:
            return
            
        self.is_monitoring = False
        self.stop_monitoring_event.set()
        
        if self.silence_timer:
            self.silence_timer.cancel()
            
        if self.vad_thread:
            self.vad_thread.join()
            
        logger.debug("VAD monitoring stopped")
        
    def _vad_monitoring_loop(self):
        while self.is_monitoring and not self.stop_monitoring_event.is_set():
            try:
                speech_detected = self._detect_speech()
                
                with self.state_lock:
                    if speech_detected:
                        self.last_speech_time = datetime.now()
                        self._handle_speech_detected()
                    else:
                        self._handle_no_speech()
                        
                time.sleep(self.vad_check_interval)
                
            except Exception as e:
                logger.error(f"Erreur dans la boucle VAD: {e}")
                time.sleep(1)
                
    def _detect_speech(self) -> bool:
        chunks = self.audio_capture.buffer.get_all_chunks()
        if not chunks:
            return False
            
        min_chunks_required = int((1.0 * self.audio_capture.SAMPLE_RATE) / self.audio_capture.FRAMES_PER_BUFFER)
        
        if len(chunks) < min_chunks_required:
            return False
        
        try:
            audio_data = []
            for chunk_info in chunks[-min_chunks_required:]:
                chunk_bytes = base64.b64decode(chunk_info['base64'])
                chunk_samples = np.frombuffer(chunk_bytes, dtype=np.int16)
                audio_data.extend(chunk_samples)
            
            audio_array = np.array(audio_data, dtype=np.float32)
            audio_tensor = torch.FloatTensor(audio_array) / 32768.0
            
            if len(audio_tensor) == 0:
                return False
                
            speech_timestamps = get_speech_timestamps(
                audio_tensor,
                self.vad_model,
                return_seconds=True,
                sampling_rate=self.audio_capture.SAMPLE_RATE,
                min_speech_duration_ms=200,
                min_silence_duration_ms=100,
                window_size_samples=1536,
                speech_pad_ms=30
            )
            
            return bool(speech_timestamps)
            
        except Exception as e:
            logger.error(f"Erreur lors de la détection VAD: {e}")
            return False
            
    def _handle_speech_detected(self):
        if self.state == TranscriptionState.IDLE:
            self.state = TranscriptionState.TRANSCRIBING
            self._start_transcription()
            
        elif self.state == TranscriptionState.WAITING_SILENCE:
            logger.debug("Speech detected - Cancelling silence timeout")
            self.state = TranscriptionState.TRANSCRIBING
            if self.silence_timer:
                self.silence_timer.cancel()
                self.silence_timer = None
                
    def _handle_no_speech(self):
        if self.state == TranscriptionState.TRANSCRIBING:
            logger.debug(f"Silence detected - Starting {self.silence_timeout}s timeout")
            self.state = TranscriptionState.WAITING_SILENCE
            self._start_silence_timer()
            
    def _start_transcription(self):
        def run_transcription():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self.transcription_service.run_transcription())
            finally:
                loop.close()
                
        transcription_thread = threading.Thread(target=run_transcription)
        transcription_thread.daemon = True
        transcription_thread.start()
        
    def _start_silence_timer(self):
        if self.silence_timer:
            self.silence_timer.cancel()
            
        self.silence_timer = threading.Timer(self.silence_timeout, self._on_silence_timeout)
        self.silence_timer.start()
        
    def _on_silence_timeout(self):
        with self.state_lock:
            if self.state == TranscriptionState.WAITING_SILENCE:
                logger.info("Silence timeout reached - Stopping transcription")
                self.state = TranscriptionState.IDLE
                self.transcription_service.stop_transcription()
                
    def get_status(self):
        with self.state_lock:
            return {
                'state': self.state.value,
                'is_monitoring': self.is_monitoring,
                'last_speech_time': self.last_speech_time.isoformat() if self.last_speech_time else None,
                'is_transcribing': self.transcription_service.is_transcribing
            }

class GladiaAudioManager:
    def __init__(self, gladia_key: str, silence_timeout: float = 30.0, prebuffer_seconds: float = 3.0):
        self.audio_buffer = SharedAudioBuffer(prebuffer_seconds=prebuffer_seconds)
        self.audio_capture = AudioCapture(self.audio_buffer)
        self.transcription_service = TranscriptionService(self.audio_buffer, gladia_key)
        self.vad_model = load_silero_vad()
        self.vad_controller = VADTranscriptionController(
            self.audio_capture, 
            self.transcription_service, 
            self.vad_model,
            silence_timeout
        )
        
    def start_audio_capture(self):
        logger.debug(f"Starting audio capture with {self.audio_buffer.prebuffer_seconds}s pre-buffer...")
        self.audio_capture.start_capture()
        return self
        
    def stop_audio_capture(self):
        logger.debug("Stopping audio capture...")
        self.audio_capture.stop_capture()
        
    def start_vad_monitoring(self):
        self.vad_controller.start_monitoring()
        return self
        
    def stop_vad_monitoring(self):
        self.vad_controller.stop_monitoring()
        
    def get_buffer_stats(self):
        chunks = self.audio_buffer.get_all_chunks()
        prebuffer_chunks = self.audio_buffer.get_prebuffer_chunks()
        return {
            'total_chunks': len(chunks),
            'prebuffer_chunks': len(prebuffer_chunks),
            'prebuffer_seconds': self.audio_buffer.prebuffer_seconds,
            'is_recording': self.audio_buffer.is_recording,
            'listeners': len(self.audio_buffer.listeners)
        }
        
    def get_vad_status(self):
        return self.vad_controller.get_status()
        
    def cleanup(self):
        self.vad_controller.stop_monitoring()
        self.audio_capture.cleanup()

def get_audio_devices():
    p = pyaudio.PyAudio()
    input_devices = []
    output_devices = []
    
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if int(info['maxInputChannels']) > 0:
            input_devices.append(AudioDevice(
                index=i,
                name=str(info['name']),
                sample_rate=int(info['defaultSampleRate']),
                max_channels=int(info['maxInputChannels'])
            ))
        if int(info['maxOutputChannels']) > 0:
            output_devices.append(AudioDevice(
                index=i,
                name=str(info['name']),
                sample_rate=int(info['defaultSampleRate']),
                max_channels=int(info['maxOutputChannels'])
            ))
    
    p.terminate()
    
    return input_devices, output_devices

async def main(allowed_languages: List[str] = ["auto", "fr", "en", "es", "de"], silence_timeout: float = 30.0, prebuffer_seconds: float = 3.0, debug: bool = False):
    parser = argparse.ArgumentParser()

    parser.add_argument("--agent-device",   type=get_device_info,      help="USB device name for agent mic (e.g., 'Logitech').")
    parser.add_argument("--agent-language", choices=allowed_languages, default="auto", help="Language spoken by the agent.")
    parser.add_argument("--phone-device",   type=get_device_info,      help="USB device name for phone mic.")
    parser.add_argument("--phone-language", choices=allowed_languages, default="auto", help="Language spoken by the phone.")
    parser.add_argument("--gladia-key",     type=str,                  help="Gladia API key. If omitted, will try the 'GLADIA_KEY' environment variable.")

    args = parser.parse_args()

    gladia_key = args.gladia_key or os.getenv("GLADIA_API_KEY")
    gladia_key = is_gladia_key_valid(gladia_key)

    if debug:
        logger.setLevel(logging.DEBUG)
        
    print(f"===== Gladia Live Transcription + Auto VAD + Pre-buffer ({prebuffer_seconds}s) =====")
    
    manager = GladiaAudioManager(gladia_key, silence_timeout, prebuffer_seconds).start_audio_capture().start_vad_monitoring()
    logger.info('Starting transcription...')

    try:
        while True:
            input()
            await asyncio.sleep(0.1)
    except KeyboardInterrupt:
        logger.info("Program interrupted by user.")
    finally:
        manager.cleanup()
        logger.info("Goodbye!")

async def synthesize_text():
    text = """Bonjour Max, j'espère que ta journée se passe bien. Aujourd'hui, nous allons tester la synthèse vocale avec une voix masculine."""
    voice = "fr-FR-DenisNeural"
    communicate = edge_tts.Communicate(text=text, voice=voice)
    await communicate.save("output.mp3")

if __name__ == "__main__":
    try:
        asyncio.run(main())
        #  asyncio.run(synthesize_text())
    except Exception as e:
        print(f"Erreur inattendue: {e}")
