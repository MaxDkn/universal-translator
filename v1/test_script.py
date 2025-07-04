import os
import time
import json
import torch
import base64
import aiohttp
import logging
import pyaudio
import asyncio
import requests
import datetime
import argparse
import threading
import numpy as np

from collections import deque
from websockets.exceptions import ConnectionClosedOK
from silero_vad import load_silero_vad, get_speech_timestamps
from typing import Callable, List, Literal, TypedDict, Optional
from websockets.asyncio.client import ClientConnection, connect
from custom_exceptions import AudioStreamStartException, DeviceNotFoundException, InvalidGladiaKeyException


logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:     %(asctime)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

logger = logging.getLogger(__name__)
for noisy_logger in ["urllib3", "torch", "pyaudio", "requests"]:
    logging.getLogger(noisy_logger).setLevel(logging.ERROR)


class InitiateResponse(TypedDict):
    id: str
    url: str


class LanguageConfiguration(TypedDict):
    languages: list[str] | None
    code_switching: bool | None


class StreamingConfiguration(TypedDict):
    # https://docs.gladia.io/api-reference/v2/live/init
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


def get_device_info(device_name: str) -> AudioDevice:
    """Find audio device info by USB device name with fallback options"""
    p = pyaudio.PyAudio()
    
    candidates = []
    
    for i in range(p.get_device_count()):
        try:
            device_info = p.get_device_info_by_index(i)
            if device_info['maxInputChannels'] > 0:  # Input device
                if device_name.lower() in device_info['name'].lower():
                    candidates.append(AudioDevice(
                        index=i,
                        name=device_info['name'],
                        sample_rate=int(device_info['defaultSampleRate']),
                        max_channels=device_info['maxInputChannels']
                    ))
        except Exception:
            continue
    
    p.terminate()
    
    if not candidates:
        raise DeviceNotFoundException(device_name)
    
    return max(candidates, key=lambda x: x.sample_rate)


def test_device_configuration(device_index: int, sample_rate: int, channels: int = 1) -> bool:
    """Test if device configuration is valid"""
    p = pyaudio.PyAudio()
    
    try:
        # Test if configuration is supported
        supported = p.is_format_supported(
            rate=sample_rate,
            input_device=device_index,
            input_channels=channels,
            input_format=pyaudio.paInt16
        )
        p.terminate()
        return supported
    except:
        p.terminate()
        return False


class AudioBuffer:
    def __init__(self, device: AudioDevice, preferred_sample_rate: int = 16_000, 
                 chunk_size: int = 1024, buffer_seconds: int = 3):
        self.device_info = device
        self.chunk_size = chunk_size
        
        # Configuration du sample rate
        possible_rates = [preferred_sample_rate, 44100, 48000, 22050, 8000]
        self.sample_rate = None

        for rate in possible_rates:
            if test_device_configuration(self.device_info.index, rate):
                self.sample_rate = rate
                break
        
        if self.sample_rate is None:
            self.sample_rate = int(self.device_info.sample_rate)

        # Buffer circulaire thread-safe
        self.data = deque(maxlen=self.sample_rate * buffer_seconds)
        self.lock = threading.Lock()
        
        # Stream PyAudio
        self.p = None
        self.stream = None

    def start_stream(self):
        """Démarre le stream audio avec callback"""
        self.p = pyaudio.PyAudio()
        
        try:
            self.stream = self.p.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.sample_rate,
                input=True,
                input_device_index=self.device_info.index,
                frames_per_buffer=self.chunk_size,
                stream_callback=self._audio_callback
            )
            self.stream.start_stream()
            
        except Exception as e:
            self.p.terminate()
            raise AudioStreamStartException(self.device_info.name, str(e)) from e
        
        return self

    def _audio_callback(self, in_data, *_):
        """Callback appelé automatiquement par PyAudio"""
        try:
            audio_data = np.frombuffer(in_data, dtype=np.int16)
            with self.lock:
                self.data.extend(audio_data)
        except Exception as e:
            logger.error(f"Audio callback error: {e}")        
        return (in_data, pyaudio.paContinue)

    def get_chunk(self, chunk_size: int = 3200) -> Optional[bytes]:
        """
        Extrait un chunk de données audio du buffer de manière thread-safe
        
        Args:
            chunk_size: Nombre d'échantillons à extraire
            
        Returns:
            bytes: Données audio en format int16, ou None si pas assez de données
        """
        with self.lock:
            if len(self.data) >= chunk_size:
                # Extraire les données du buffer
                chunk_data = []
                for _ in range(chunk_size):
                    if self.data:
                        chunk_data.append(self.data.popleft())
                    else:
                        break
                
                if chunk_data:
                    # Convertir en bytes
                    chunk_array = np.array(chunk_data, dtype=np.int16)
                    return chunk_array.tobytes()
        
        return None

    def get_buffer_size(self) -> int:
        """Retourne la taille actuelle du buffer"""
        with self.lock:
            return len(self.data)

    def stop_stream(self):
        """Arrête le stream audio"""
        if hasattr(self, 'stream') and self.stream:
            self.stream.stop_stream()
            self.stream.close()
        if hasattr(self, 'p') and self.p:
            self.p.terminate()


def list_audio_devices():
    """List all available audio input devices"""
    p = pyaudio.PyAudio()
    devices = []   
    for i in range(p.get_device_count()):
        try:
            device_info = p.get_device_info_by_index(i)
            if device_info['maxInputChannels'] > 0:
                devices.append((i, device_info['name'], device_info['defaultSampleRate']))
        except:
            continue
    
    p.terminate()
    return devices


def is_gladia_key_valid(key: str, url: str = "https://api.gladia.io/v2/pre-recorded") -> str:
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
    if response.status_code != 200:
        raise InvalidGladiaKeyException()
    return key


def format_duration(seconds: float) -> str:
    """Formate une durée en secondes au format HH:MM:SS.mmm"""
    milliseconds = int(seconds * 1000)
    return datetime.time(
        hour=milliseconds // 3_600_000,
        minute=(milliseconds // 60_000) % 60,
        second=(milliseconds // 1_000) % 60,
        microsecond=(milliseconds % 1_000) * 1_000,
    ).isoformat(timespec="milliseconds")


def detect_speech(audio_buffer: AudioBuffer, model: object) -> bool:
    """
    Detects speech activity in an audio buffer using a voice activity detection (VAD) model.

    Args:
        audio_buffer (AudioBuffer): Buffer contenant les échantillons audio.
        model: The Silero VAD model used for speech detection.

    Returns:
        bool: True if speech is detected in the buffer, False otherwise.
    """
    if len(audio_buffer.data) < audio_buffer.sample_rate:
        return False
        
    try:
        # Convert to tensor for Silero VAD
        audio_array = np.array(list(audio_buffer.data), dtype=np.float32)
        audio_tensor = torch.FloatTensor(audio_array) / 32768.0
        
        # Resample if necessary for VAD (Silero expects 16kHz)
        if audio_buffer.sample_rate != 16000:
            # Simple downsampling for VAD
            step = audio_buffer.sample_rate // 16000
            audio_tensor = audio_tensor[::step]
        
        speech_timestamps = get_speech_timestamps(
            audio_tensor,
            model,
            return_seconds=True,
            sampling_rate=16000
        )
        
        return bool(speech_timestamps)
        
    except Exception as e:
        logger.error(f"VAD detection error: {e}")
        return False


async def init_live_session(config: StreamingConfiguration, gladia_key: str) -> InitiateResponse:
    """Initialise une session Gladia Live"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.gladia.io/v2/live",
            headers={"X-Gladia-Key": gladia_key},
            json=config,
            timeout=3
        ) as response:
            if response.status not in (200, 201):
                text = await response.text()
                logger.error(f"{response.status}: {text or response.reason}")
                raise Exception(f"Erreur init session: {response.status}")
            return await response.json()


async def receive_messages(websocket: ClientConnection):
    """Traite les messages reçus du WebSocket"""
    try:
        async for message in websocket:
            content = json.loads(message)
            logger.info(content)
            
            if content["type"] == "transcript" and content["data"]["is_final"]:
                start = format_duration(content["data"]["utterance"]["start"])
                end = format_duration(content["data"]["utterance"]["end"])
                text = content["data"]["utterance"]["text"].strip()
                print(f"🎤 {start} --> {end} | {text}")
            
            elif content["type"] == "post_final_transcript":
                print("\n################ Session terminée ################")
                print(json.dumps(content, indent=2, ensure_ascii=False))
                
    except ConnectionClosedOK:
        logger.info("Connexion WebSocket fermée normalement")
    except Exception as e:
        logger.error(f"Erreur réception messages: {e}")


async def send_audio_chunk(websocket: ClientConnection, audio_buffer: AudioBuffer):
    """Envoie un chunk audio si disponible"""
    chunk_bytes = audio_buffer.get_chunk(3200)
    
    if chunk_bytes:
        # Encoder en base64
        encoded_data = base64.b64encode(chunk_bytes).decode("utf-8")
        
        json_data = json.dumps({
            "type": "audio_chunk", 
            "data": {"chunk": encoded_data}
        })
        
        await websocket.send(json_data)
        logger.debug(f"📤 Chunk envoyé ({len(chunk_bytes)} bytes)")
        return True
    return False


async def main(*, allowed_languages: List[str] = ["auto", "fr", "en", "es", "de"],
                silence_timeout_seconds: int = 30) -> None:
    """
    Main entry point: parses CLI args, validates Gladia key, sets up VAD and monitors speech.
    """
    parser = argparse.ArgumentParser()

    parser.add_argument("--agent-device", type=get_device_info, help="USB device name for agent mic (e.g., 'Logitech').")
    parser.add_argument("--agent-language", choices=allowed_languages, default="auto", help="Language spoken by the agent.")
    parser.add_argument("--phone-device", type=get_device_info, help="USB device name for phone mic.")
    parser.add_argument("--phone-language", choices=allowed_languages, default="auto", help="Language spoken by the phone.")
    parser.add_argument("--gladia-key", type=str, help="Gladia API key. If omitted, will try the 'GLADIA_KEY' environment variable.")

    args = parser.parse_args()

    gladia_key = args.gladia_key or os.getenv("GLADIA_API_KEY")
    gladia_key = is_gladia_key_valid(gladia_key)

    # Configuration streaming
    STREAMING_CONFIGURATION: StreamingConfiguration = {
        "encoding": "wav/pcm",
        "sample_rate": 16000,
        "bit_depth": 16,
        "channels": 1,
        "language_config": {
            "languages": [],
            "code_switching": True,
        },
    }

    # Initialisation
    vad_model = load_silero_vad()
    audio_buffer = AudioBuffer(args.agent_device).start_stream()
    
    websocket: Optional[ClientConnection] = None
    session_active = False
    silence_start: Optional[float] = None
    receive_task: Optional[asyncio.Task] = None
    
    logger.info("System started - Waiting for the voice...")
    
    try:
        while True:
            # Détection de parole
            is_speaking = detect_speech(audio_buffer, vad_model)
            print(f'\r{is_speaking}', end='')
            
            if session_active and websocket:
                await send_audio_chunk(websocket, audio_buffer)
                logger.info('Test audio chunk sent')


            if is_speaking:
                # Parole détectée
                if not session_active:
                    logger.info("Voice detected - Websocket session started...")
                    
                    session_data = await init_live_session(STREAMING_CONFIGURATION, gladia_key)
                    
                    websocket = await connect(session_data["url"])
                    session_active = True
                    
                    receive_task = asyncio.create_task(receive_messages(websocket))
                    
                    logger.info("Session WebSocket active")
                if silence_start is not None:
                    logger.info('Fin du silence')
                    silence_start = None
                
                    
            else:
                if session_active:
                    if silence_start is None:
                        silence_start = time.time()
                        logger.info("Début du silence")
                    
                    elif time.time() - silence_start > silence_timeout_seconds:
                        logger.info(f"Timeout silence ({silence_timeout_seconds}s) - Fermeture session")
                        
                        if websocket:
                            await websocket.send(json.dumps({"type": "stop_recording"}))
                            await asyncio.sleep(0.1)
                            
                            await websocket.close()
                            websocket = None
                        
                        if receive_task and not receive_task.done():
                            receive_task.cancel()
                            try:
                                await receive_task
                            except asyncio.CancelledError:
                                pass
                        
                        session_active = False
                        silence_start = None
                        logger.info("Session fermée")
            
            await asyncio.sleep(0.1)
            
    except KeyboardInterrupt:
        logger.info("Arrêt demandé par l'utilisateur")
    finally:
        if session_active and websocket:
            try:
                await websocket.send(json.dumps({"type": "stop_recording"}))
                await websocket.close()
            except:
                pass
        
        if receive_task and not receive_task.done():
            receive_task.cancel()
            try:
                await receive_task
            except asyncio.CancelledError:
                pass
        
        audio_buffer.stop_stream()
        logger.info('Système arrêté')


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
