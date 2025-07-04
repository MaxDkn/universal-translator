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
        with self.lock:
            return len(self.data)

    def stop_stream(self):
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


CHANNELS = 1

FRAMES_PER_BUFFER = 3200
SAMPLE_RATE = 16_000


STREAMING_CONFIGURATION: StreamingConfiguration = {
    "encoding": "wav/pcm",
    "sample_rate": SAMPLE_RATE,
    "bit_depth": 16,  # It should match the FORMAT value
    "channels": CHANNELS,
    "language_config": {
        "languages": [],
        "code_switching": True,
    },
}


def format_duration(seconds: float) -> str:
    """Formate une durée en secondes vers le format HH:MM:SS.mmm"""
    milliseconds = int(seconds * 1_000)
    return datetime.time(
        hour=milliseconds // 3_600_000,
        minute=(milliseconds // 60_000) % 60,
        second=(milliseconds // 1_000) % 60,
        microsecond=milliseconds % 1_000 * 1_000,
    ).isoformat(timespec="milliseconds")


class WebSocket:
    def __init__(self, gladia_key: str, config):
        self.gladia_key = gladia_key
        self.config = config
        
        self.socket: Optional[ClientConnection] = None

    async def create(self) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.gladia.io/v2/live",
                headers={"X-Gladia-Key": self.gladia_key},
                json=self.config,
                timeout=3
            ) as response:
                if not response.ok:
                    text = await response.text()
                    logger.error(f"{response.status}: {text or response.reason}")
                    exit(response.status)
                response = await response.json()
                self.socket = await connect(response["url"])

    async def stop(self) -> None:
        if self.socket:
            await self.socket.send(json.dumps({"type:": "stop_recording"}))
            await asyncio.sleep(0)
        self.socket = None

    async def send_audio_chunk(self, audio_buffer: AudioBuffer, 
                               chunk_size_duration_ms: int = 300) -> bool:
        """
        Envoie un chunk audio du buffer via WebSocket
        
        Args:
            socket: Connexion WebSocket
            audio_buffer: Instance de AudioBuffer
            chunk_size: Taille du chunk à envoyer (en échantillons)
            
        Returns:
            bool: True si un chunk a été envoyé, False sinon
        """
        chunk_size = int(audio_buffer.sample_rate * chunk_size_duration_ms / 1000)
        chunk_data = audio_buffer.get_chunk(chunk_size)
        
        if chunk_data is not None:
            # Encoder en base64 pour l'envoi JSON
            encoded_data = base64.b64encode(chunk_data).decode("utf-8")
            
            # Préparer le message JSON
            json_data = json.dumps({
                "type": "audio_chunk", 
                "data": {
                    "chunk": str(encoded_data),
                    "sample_rate": audio_buffer.sample_rate,
                    "chunk_size": len(chunk_data)
                }
            })

            if self.socket:
                # Envoyer via WebSocket
                await self.socket.send(json_data)
                return True
        
        return False
    async def display_messages(self) -> None:
        """
        Affiche les messages reçus du WebSocket en temps réel
        """
        if not self.socket:
            print("Erreur: WebSocket non connecté")
            return
            
        try:
            async for message in self.socket:
                content = json.loads(message)
                
                # Afficher les transcriptions finales
                if content["type"] == "transcript" and content["data"]["is_final"]:
                    start = format_duration(content["data"]["utterance"]["start"])
                    end = format_duration(content["data"]["utterance"]["end"])
                    text = content["data"]["utterance"]["text"].strip()
                    print(f"{start} --> {end} | {text}")
                
                # Afficher les transcriptions intermédiaires (optionnel)
                elif content["type"] == "transcript" and not content["data"]["is_final"]:
                    text = content["data"]["utterance"]["text"].strip()
                    print(f"[En cours] {text}", end='\r')  # Écrasement de ligne
                
                # Afficher la transcription finale de fin de session
                elif content["type"] == "post_final_transcript":
                    print("\n################ End of session ################\n")
                    print(json.dumps(content, indent=2, ensure_ascii=False))
                    break
                    
                # Gérer d'autres types de messages si nécessaire
                elif content["type"] == "error":
                    print(f"Erreur: {content.get('message', 'Erreur inconnue')}")
                    
        except ConnectionClosedOK:
            print("Connexion WebSocket fermée proprement")
        except Exception as e:
            print(f"Erreur lors de la lecture des messages: {e}")

    async def listen_for_messages(self) -> None:
        """
        Version non-bloquante pour écouter les messages en arrière-plan
        """
        await self.display_messages()


class WSSessionManager:
    def __init__(self, gladia_key: str, silence_timeout: int = 90):
        self.gladia_key = gladia_key
        self.silence_timeout = silence_timeout
        
        # État de la session
        self.websocket: Optional[ClientConnection] = None
        self.session_url: Optional[str] = None
        self.silence_start: Optional[float] = None
        self.is_session_active = False
        
        # Tâches asynchrones
        self.send_audio_task: Optional[asyncio.Task] = None
        self.recv_messages_task: Optional[asyncio.Task] = None
        
        # Configuration streaming
        self.streaming_config = {
            "encoding": "wav/pcm",
            "sample_rate": 16000,
            "bit_depth": 16,
            "channels": 1,
            "language_config": {
                "languages": [],
                "code_switching": True,
            },
        }

    async def _init_session(self) -> str:
        """Initialise une nouvelle session Gladia et retourne l'URL WebSocket"""
        import aiohttp
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.gladia.io/v2/live",
                headers={"X-Gladia-Key": self.gladia_key},
                json=self.streaming_config,
                timeout=3
            ) as response:
                if response.status not in (200, 201):
                    text = await response.text()
                    logger.error(f"Erreur init session: {response.status} - {text}")
                    raise Exception(f"Erreur init session: {response.status}")
                
                data = await response.json()
                return data["url"]

    async def _send_audio_loop(self, audio_buffer):
        """Boucle d'envoi audio en continu depuis le buffer callback"""
        logger.info('Test')
        try:
            chunk_size = 3200  # Taille de chunk pour l'envoi
            
            while self.is_session_active and self.websocket:
                try:
                    # Récupérer un chunk depuis le buffer
                    chunk_bytes = audio_buffer.get_chunk(chunk_size)
                    
                    if chunk_bytes:
                        # Encoder en base64
                        encoded_data = base64.b64encode(chunk_bytes).decode("utf-8")
                        
                        json_data = json.dumps({
                            "type": "audio_chunk", 
                            "data": {"chunk": encoded_data}
                        })
                        
                        await self.websocket.send(json_data)
                        logger.info(f"📤 Chunk envoyé ({len(chunk_bytes)} bytes)")
                    
                    await asyncio.sleep(0.1)  # 100ms entre chaque envoi
                        
                except Exception as e:
                    logger.error(f"Erreur envoi audio: {e}")
                    break
                    
        except ConnectionClosedOK:
            logger.info("Connexion WebSocket fermée normalement")
        except Exception as e:
            logger.error(f"Erreur dans send_audio_loop: {e}")

    def _format_duration(self, seconds: float) -> str:
        """Formate une durée en secondes au format HH:MM:SS.mmm"""
        milliseconds = int(seconds * 1000)
        return datetime.time(
            hour=milliseconds // 3_600_000,
            minute=(milliseconds // 60_000) % 60,
            second=(milliseconds // 1_000) % 60,
            microsecond=(milliseconds % 1_000) * 1_000,
        ).isoformat(timespec="milliseconds")

    async def _receive_messages_loop(self):
        """Boucle de réception et affichage des messages"""
        try:
            logger.info('Recieve message', self.websocket)
            async for message in self.websocket:
                content = json.loads(message)
                
                if content["type"] == "transcript" and content["data"]["is_final"]:
                    start = self._format_duration(content["data"]["utterance"]["start"])
                    end = self._format_duration(content["data"]["utterance"]["end"])
                    text = content["data"]["utterance"]["text"].strip()
                    print(f"🎤 {start} --> {end} | {text}")
                
                elif content["type"] == "post_final_transcript":
                    print("\nSession terminée")
                    print(json.dumps(content, indent=2, ensure_ascii=False))
                    
        except ConnectionClosedOK:
            logger.info("Connexion WebSocket fermée")
        except Exception as e:
            logger.error(f"Erreur réception messages: {e}")

    async def _start_session(self, audio_buffer):
        try:
            logger.info("Starting WebSocket Session...")
            
            self.session_url = await self._init_session()
            
            self.websocket = await connect(self.session_url)
            self.is_session_active = True
            
            # Démarrer les tâches asynchrones
            self.send_audio_task = asyncio.create_task(
                self._send_audio_loop(audio_buffer)
            )
            self.recv_messages_task = asyncio.create_task(
                self._receive_messages_loop()
            )
            
            logger.info("✅ Session WebSocket démarrée")
            
        except Exception as e:
            logger.error(f"Erreur démarrage session: {e}")
            await self._cleanup_session()

    async def _stop_session(self):
        """Arrête la session WebSocket proprement"""
        if not self.is_session_active:
            return
            
        logger.info("🛑 Arrêt session WebSocket...")
        
        try:
            # Envoyer signal d'arrêt
            if self.websocket:
                await self.websocket.send(json.dumps({"type": "stop_recording"}))
                await asyncio.sleep(0.1)
                
        except Exception as e:
            logger.error(f"Erreur lors de l'arrêt: {e}")
        finally:
            await self._cleanup_session()

    async def _cleanup_session(self):
        """Nettoie les ressources de la session"""
        self.is_session_active = False
        
        # Annuler les tâches
        if self.send_audio_task and not self.send_audio_task.done():
            self.send_audio_task.cancel()
            try:
                await self.send_audio_task
            except asyncio.CancelledError:
                pass
                
        if self.recv_messages_task and not self.recv_messages_task.done():
            self.recv_messages_task.cancel()
            try:
                await self.recv_messages_task
            except asyncio.CancelledError:
                pass
        
        # Fermer WebSocket
        if self.websocket:
            try:
                await self.websocket.close()
            except:
                pass
            self.websocket = None
            
        self.session_url = None
        self.silence_start = None
        
        logger.info("🧹 Session nettoyée")

    async def manage(self, audio_buffer, is_speaking: bool):
        """
        Méthode principale à appeler dans la boucle principale.
        Gère automatiquement l'ouverture/fermeture des sessions selon l'activité vocale.
        """
        if is_speaking:
            if not self.is_session_active:
                await self._start_session(audio_buffer)
            
            self.silence_start = None
            
        else:
            # Pas de parole
            if self.is_session_active:
                # Commencer à compter le silence
                if self.silence_start is None:
                    self.silence_start = time.time()
                    logger.debug("🔇 Début du silence")
                
                # Vérifier si le timeout est atteint
                elif time.time() - self.silence_start > self.silence_timeout:
                    logger.info(f"⏰ Timeout silence ({self.silence_timeout}s) atteint")
                    await self._stop_session()

    async def cleanup(self):
        """Nettoyage final à appeler avant la fermeture du programme"""
        await self._cleanup_session()


def detect_speech(audio_buffer: AudioBuffer, model: object) -> bool:
        """
        Detects speech activity in an audio buffer using a voice activity detection (VAD) model.

        Args:
            audio_buffer (deque): A deque containing raw PCM 16-bit mono audio samples.
            model: The Silero VAD model used for speech detection.

        Returns:
            bool: True if speech is detected in the buffer, False otherwise.

        Notes:
            - The function expects the audio buffer to contain at least 1 second of audio.
            - If the sample rate is not 16 kHz, the audio is downsampled to match the Silero model's expected input.
            - Audio samples are normalized to the [-1.0, 1.0] float32 range before inference.
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


async def main(*, allowed_languages: List[str] = ["auto", "fr", "en", "es", "de"],
                silence_timeout_seconds: int = 30) -> None:
    """
    Main entry point: parses CLI args, validates Gladia key, sets up VAD and monitors speech.

    Args:
        allowed_languages (List[str]): Supported language codes.
        load_model (object): Function to load the Silero VAD model.
        silence_timeout_seconds (int): Duration (in seconds) of continuous silence before taking action.

    Raises:
        DeviceNotFoundException: If the specified USB device is not found.
        InvalidGladiaKeyException: If the Gladia API key is invalid.
    """
    parser = argparse.ArgumentParser()

    parser.add_argument("--agent-device",   type=get_device_info,      help="USB device name for agent mic (e.g., 'Logitech').")
    parser.add_argument("--agent-language", choices=allowed_languages, default="auto", help="Language spoken by the agent.")
    parser.add_argument("--phone-device",   type=get_device_info,      help="USB device name for phone mic.")
    parser.add_argument("--phone-language", choices=allowed_languages, default="auto", help="Language spoken by the phone.")
    parser.add_argument("--gladia-key",     type=str,                  help="Gladia API key. If omitted, will try the 'GLADIA_KEY' environment variable.")

    args = parser.parse_args()

    gladia_key = args.gladia_key or os.getenv("GLADIA_API_KEY")
    gladia_key = is_gladia_key_valid(gladia_key)

    vad_model = load_silero_vad()
    audio_buffer = AudioBuffer(args.agent_device).start_stream()
    #  wss_manager = WSSessionManager(gladia_key, silence_timeout_seconds)
    #  vad_detector = setup_realtime_vad(args.agent_device.name, model)
    #  wss_manager = WSSessionManager(STREAMING_CONFIGURATION, gladia_key, silence_timeout_seconds)
    websocket = WebSocket(gladia_key, STREAMING_CONFIGURATION)
    
    try:
        while True:
            is_speaking = detect_speech(audio_buffer, vad_model)
            if websocket.is_new_message():
                print(websocket.new_message)
            print(f'\r{int(is_speaking)}', end='')
            
            if websocket.socket:
                logger.info('going to send data')
                if await websocket.send_audio_chunk(audio_buffer):
                    logger.info(audio_buffer.get_buffer_size())
                
            else: 
                logger.info('create session')
                await websocket.create()
                message_task = asyncio.create_task(websocket.listen_for_messages())
                logger.info('session created')
                
            await asyncio.sleep(0.1)
        
    except KeyboardInterrupt:
        pass
    finally:
        #  await wss_manager.cleanup()
        await websocket.listen_for_messages()
        await websocket.stop()
        audio_buffer.stop_stream()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
