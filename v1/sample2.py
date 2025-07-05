import asyncio
import base64
import json
import sys
import threading
import time
from collections import deque
from datetime import datetime
from typing import Literal, TypedDict, Optional
import queue

import pyaudio
import requests
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosedOK

GLADIA_API_URL = "https://api.gladia.io"

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

class SharedAudioBuffer:
    def __init__(self, max_chunks: int = 1000):
        self.buffer = deque(maxlen=max_chunks)
        self.lock = threading.Lock()
        self.is_recording = False
        self.listeners = []
        self.chunk_queue = queue.Queue()
        
    def start_recording(self):
        with self.lock:
            self.is_recording = True
            self.buffer.clear()
            
    def stop_recording(self):
        with self.lock:
            self.is_recording = False
            
    def add_chunk(self, chunk_data: bytes):
        with self.lock:
            if self.is_recording:
                timestamp = datetime.now()
                chunk_info = {
                    'data': chunk_data,
                    'timestamp': timestamp,
                    'base64': base64.b64encode(chunk_data).decode('utf-8')
                }
                self.buffer.append(chunk_info)
                
                for listener in self.listeners:
                    try:
                        listener.put_nowait(chunk_info)
                    except queue.Full:
                        pass
                        
    def get_recent_chunks(self, count: int = 10) -> list:
        with self.lock:
            return list(self.buffer)[-count:] if len(self.buffer) >= count else list(self.buffer)
            
    def get_all_chunks(self) -> list:
        with self.lock:
            return list(self.buffer)
            
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
        self.buffer.start_recording()
        
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
        self.buffer.stop_recording()
        
        if self.thread:
            self.thread.join()
            
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            
    def _capture_loop(self):
        while self.is_running:
            try:
                data = self.stream.read(self.FRAMES_PER_BUFFER, exception_on_overflow=False)
                self.buffer.add_chunk(data)
                time.sleep(0.01)
            except Exception as e:
                print(f"Erreur capture audio: {e}")
                break
                
    def cleanup(self):
        self.stop_capture()
        self.p.terminate()

class TranscriptionService:
    def __init__(self, buffer: SharedAudioBuffer):
        self.buffer = buffer
        self.is_transcribing = False
        self.listener_queue = None
        
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
        
    def get_gladia_key(self) -> str:
        if len(sys.argv) != 2 or not sys.argv[1]:
            print("You must provide a Gladia key as the first argument.")
            exit(1)
        return sys.argv[1]
        
    def init_live_session(self) -> InitiateResponse:
        gladia_key = self.get_gladia_key()
        response = requests.post(
            f"{GLADIA_API_URL}/v2/live",
            headers={"X-Gladia-Key": gladia_key},
            json=self.STREAMING_CONFIGURATION,
            timeout=3,
        )
        if not response.ok:
            print(f"{response.status_code}: {response.text or response.reason}")
            exit(response.status_code)
        return response.json()
        
    async def print_messages_from_socket(self, socket: ClientConnection) -> None:
        async for message in socket:
            content = json.loads(message)
            if content["type"] == "transcript" and content["data"]["is_final"]:
                text = content["data"]["utterance"]["text"].strip()
                print(f"text: {text}")
            if content["type"] == "post_final_transcript":
                print("\n################ End of session ################\n")
                print(json.dumps(content, indent=2, ensure_ascii=False))
                self.is_transcribing = False
                break
                
    async def send_audio_from_buffer(self, socket: ClientConnection) -> None:
        while self.is_transcribing:
            try:
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
        print(">>>>> Ending the recording…")
        await websocket.send(json.dumps({"type": "stop_recording"}))
        await asyncio.sleep(0)
        
    async def run_transcription(self):
        self.is_transcribing = True
        self.listener_queue = self.buffer.register_listener()
        
        try:
            response = self.init_live_session()
            
            async with connect(response["url"]) as websocket:
                print("\n################ Begin session ################\n")
                print("Transcription en cours depuis le buffer...")
                
                send_audio_task = asyncio.create_task(self.send_audio_from_buffer(websocket))
                print_messages_task = asyncio.create_task(self.print_messages_from_socket(websocket))
                
                done, pending = await asyncio.wait(
                    [send_audio_task, print_messages_task],
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                if self.is_transcribing:
                    await self.stop_recording(websocket)
                
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                        
        except Exception as e:
            print(f"Erreur pendant la transcription: {e}")
        finally:
            self.buffer.unregister_listener(self.listener_queue)
            print("Transcription terminée.\n")

class GladiaAudioManager:
    def __init__(self):
        self.audio_buffer = SharedAudioBuffer()
        self.audio_capture = AudioCapture(self.audio_buffer)
        self.transcription_service = TranscriptionService(self.audio_buffer)
        
    def start_audio_capture(self):
        print("Démarrage de la capture audio...")
        self.audio_capture.start_capture()
        
    def stop_audio_capture(self):
        print("Arrêt de la capture audio...")
        self.audio_capture.stop_capture()
        
    async def start_transcription(self):
        await self.transcription_service.run_transcription()
        
    def get_buffer_stats(self):
        chunks = self.audio_buffer.get_all_chunks()
        return {
            'total_chunks': len(chunks),
            'is_recording': self.audio_buffer.is_recording,
            'listeners': len(self.audio_buffer.listeners)
        }
        
    def get_recent_audio_data(self, count: int = 10):
        return self.audio_buffer.get_recent_chunks(count)
        
    def cleanup(self):
        self.audio_capture.cleanup()

def get_user_choice(prompt: str, options: list) -> str:
    while True:
        try:
            choice = input(f"{prompt} ({'/'.join(options)}): ").lower().strip()
            if choice in options:
                return choice
            else:
                print(f"Réponse non reconnue. Options: {', '.join(options)}")
        except KeyboardInterrupt:
            print("\nAu revoir !")
            return "quit"

async def main():
    print("=== Gladia Live Transcription with Shared Buffer ===\n")
    
    manager = GladiaAudioManager()
    
    try:
        while True:
            print("\nOptions disponibles:")
            print("1. Démarrer capture audio")
            print("2. Arrêter capture audio")
            print("3. Démarrer transcription")
            print("4. Voir statistiques buffer")
            print("5. Quitter")
            
            choice = get_user_choice("Votre choix", ["1", "2", "3", "4", "5", "quit"])
            
            if choice in ["5", "quit"]:
                break
            elif choice == "1":
                manager.start_audio_capture()
            elif choice == "2":
                manager.stop_audio_capture()
            elif choice == "3":
                await manager.start_transcription()
            elif choice == "4":
                stats = manager.get_buffer_stats()
                print(f"Stats: {stats}")
                
    except KeyboardInterrupt:
        print("\nProgramme interrompu par l'utilisateur.")
    finally:
        manager.cleanup()
        print("Au revoir !")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"Erreur inattendue: {e}")
        