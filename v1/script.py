import os
import time
import torch
import argparse
import asyncio
import base64
import json
import datetime
from typing import Literal, TypedDict

import pyaudio
import requests
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosedOK

import requests
import logging
import base64
import pyaudio
import asyncio
import numpy as np
from typing import List
from typing import Callable
from collections import deque
from websockets.asyncio.client import ClientConnection, connect
from silero_vad import load_silero_vad, read_audio, get_speech_timestamps
from custom_exceptions import VADSetupException, AudioStreamStartException, DeviceNotFoundException, InvalidGladiaKeyException
from typing import Literal, TypedDict

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


class RobustRealTimeVAD:
    def __init__(self, device_name: str, model, preferred_sample_rate=16000, chunk_size=1024):
        self.device_info = get_device_info(device_name)
        self.model = model
        self.chunk_size = chunk_size
        self.audio_buffer = deque()
        
        # Auto-detect best sample rate
        possible_rates = [preferred_sample_rate, 44100, 48000, 22050, 8000]
        self.sample_rate = None
        
        for rate in possible_rates:
            if test_device_configuration(self.device_info.index, rate):
                self.sample_rate = rate
                break
        
        if self.sample_rate is None:
            self.sample_rate = int(self.device_info.sample_rate)
        
        # Adjust buffer size based on sample rate
        buffer_seconds = 3
        self.audio_buffer = deque(maxlen=self.sample_rate * buffer_seconds)
        
    def start_stream(self):
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

    def _audio_callback(self, in_data, frame_count, time_info, status):
        try:
            audio_data = np.frombuffer(in_data, dtype=np.int16)
            self.audio_buffer.extend(audio_data)
        except Exception as e:
            logger.error(f"Audio callback error: {e}")        
        return (in_data, pyaudio.paContinue)
    
    def detect_speech(self):
        if len(self.audio_buffer) < self.sample_rate:
            return []
            
        try:
            # Convert to tensor for Silero VAD
            audio_array = np.array(list(self.audio_buffer), dtype=np.float32)
            audio_tensor = torch.FloatTensor(audio_array) / 32768.0
            
            # Resample if necessary for VAD (Silero expects 16kHz)
            if self.sample_rate != 16000:
                # Simple downsampling for VAD
                step = self.sample_rate // 16000
                audio_tensor = audio_tensor[::step]
            
            speech_timestamps = get_speech_timestamps(
                audio_tensor,
                self.model,
                return_seconds=True,
                sampling_rate=16000
            )
            
            return speech_timestamps
            
        except Exception as e:
            logger.error(f"VAD detection error: {e}")
            return []
    
    def get_recent_audio(self, duration_seconds=2):
        """Get recent audio data for processing"""
        if len(self.audio_buffer) < self.sample_rate * duration_seconds:
            return None
            
        recent_samples = int(self.sample_rate * duration_seconds)
        audio_data = np.array(list(self.audio_buffer)[-recent_samples:], dtype=np.float32)
        return audio_data / 32768.0  # Normalize
    
    def stop_stream(self):
        if hasattr(self, 'stream'):
            self.stream.stop_stream()
            self.stream.close()
        if hasattr(self, 'p'):
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

def setup_realtime_vad(device_name: str, model):
    """
    Initialize and start real-time Voice Activity Detection (VAD) using a specified audio device.

    Args:
        device_name (str): Name or part of the name of the input audio device (e.g., "Logitech").
        model (torch.nn.Module): Preloaded Silero VAD model used for speech detection.

    Returns:
        RobustRealTimeVAD: An instance of the VAD detector with the audio stream started.

    Raises:
        VADSetupException: If the device cannot be initialized or the stream cannot be started.
    """
    try:
        vad_detector = RobustRealTimeVAD(device_name, model)
        vad_detector.start_stream()
        return vad_detector
    except Exception as e:
        raise VADSetupException(f"Unable to start VAD on device '{device_name}': {e}") from e


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


def make_wss_state_tracker(silence_timeout_seconds: int = 90) -> Callable[[bool], bool]:
    """
    Crée une fonction is_wss_open(is_speaking: bool) → bool
    qui garde un état interne et applique la logique de silence.

    :param silence_timeout_seconds: délai max de silence avant fermeture.
    :return: fonction is_wss_open(is_speaking: bool) -> bool
    """
    wss_connection_active = False
    silence_start_time = None

    def is_wss_open(is_speaking: bool) -> bool:
        nonlocal wss_connection_active, silence_start_time

        if is_speaking:
            if not wss_connection_active:
                logger.info("Speech detected, opening WSS connection.")
                wss_connection_active = True
            silence_start_time = None
        else:
            if wss_connection_active and silence_start_time is None:
                silence_start_time = time.time()
            elif wss_connection_active and silence_start_time:
                elapsed = time.time() - silence_start_time
                if elapsed > silence_timeout_seconds:
                    logger.info("Silence timeout reached, closing WSS connection.")
                    wss_connection_active = False
                    silence_start_time = None

        logger.debug(f"Speaking: {int(is_speaking)}")
        return wss_connection_active

    return is_wss_open

import aiohttp

async def init_live_session(config: StreamingConfiguration, gladia_key: str) -> InitiateResponse:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.gladia.io/v2/live",
            headers={"X-Gladia-Key": gladia_key},
            json=config,
            timeout=3
        ) as response:
            if response.status not in  (200, 201):
                text = await response.text()
                logger.error(f"{response.status}: {text or response.reason}")
                exit(response.status)
            return await response.json()
        
P = pyaudio.PyAudio()

CHANNELS = 1
FORMAT = pyaudio.paInt16
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


async def send_audio(socket: ClientConnection) -> None:
    stream = P.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=FRAMES_PER_BUFFER,
    )

    while True:
        data = stream.read(FRAMES_PER_BUFFER)
        data = base64.b64encode(data).decode("utf-8")
        json_data = json.dumps({"type": "audio_chunk", "data": {"chunk": str(data)}})
        try:
            await socket.send(json_data)
            await asyncio.sleep(0.1)  # Send audio every 100ms
        except ConnectionClosedOK:
            return


async def stop_recording(websocket: ClientConnection) -> None:
    print(">>>>> Ending the recording…")
    await websocket.send(json.dumps({"type": "stop_recording"}))
    await asyncio.sleep(0)



def format_duration(seconds: float) -> str:
    milliseconds = int(seconds * 1_000)
    return datetime.time(
        hour=milliseconds // 3_600_000,
        minute=(milliseconds // 60_000) % 60,
        second=(milliseconds // 1_000) % 60,
        microsecond=milliseconds % 1_000 * 1_000,
    ).isoformat(timespec="milliseconds")


async def print_messages_from_socket(socket: ClientConnection) -> None:
    async for message in socket:
        content = json.loads(message)
        if content["type"] == "transcript" and content["data"]["is_final"]:
            start = format_duration(content["data"]["utterance"]["start"])
            end = format_duration(content["data"]["utterance"]["end"])
            text = content["data"]["utterance"]["text"].strip()
            print(f"{start} --> {end} | {text}")
        if content["type"] == "post_final_transcript":
            print("\n################ End of session ################\n")
            print(json.dumps(content, indent=2, ensure_ascii=False))


class WSSessionManager:
    def __init__(self, streaming_config, gladia_key: str, silence_timeout: int =90):
        self.streaming_config = streaming_config
        self.websocket: ClientConnection | None = None
        self.silence_start: float | None = None
        self.silence_timeout = silence_timeout
        self.send_audio_task = None
        self.recv_text_task = None
        self.gladia_key = gladia_key

    async def start_session(self):
        response = await init_live_session(self.streaming_config, self.gladia_key)
        self.websocket = await connect(response["url"])
        logger.info("WSS session started.")
        self.send_audio_task = asyncio.create_task(send_audio(self.websocket))
        self.recv_text_task = asyncio.create_task(print_messages_from_socket(self.websocket))

    async def stop_session(self):
        if self.websocket:
            await stop_recording(self.websocket)
            await asyncio.wait([self.send_audio_task, self.recv_text_task])
            await self.websocket.close()
            logger.info("WSS session closed.")
        self.websocket = None
        self.silence_start = None

    async def manage(self, is_speaking: bool):
        if is_speaking:
            if not self.websocket:
                await self.start_session()
            self.silence_start = None
        elif self.websocket:
            if self.silence_start is None:
                self.silence_start = time.time()
            elif time.time() - self.silence_start > self.silence_timeout:
                await self.stop_session()


async def main(*, allowed_languages: List[str] = ["auto", "fr", "en", "es", "de"],
                load_model: object = load_silero_vad,
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

    parser.add_argument("-ad", "--agent-device", type=get_device_info, help="USB device name for agent mic (e.g., 'Logitech').")
    parser.add_argument("-ag", "--agent-language", choices=allowed_languages, default="auto", help="Language spoken by the agent.")
    parser.add_argument("-pd", "--phone-device", type=get_device_info, help="USB device name for phone mic.")
    parser.add_argument("-pl", "--phone-language", choices=allowed_languages, default="auto", help="Language spoken by the phone.")
    parser.add_argument("-gk", "--gladia-key", type=str, help="Gladia API key. If omitted, will try the 'GLADIA_KEY' environment variable.")

    args = parser.parse_args()

    gladia_key = args.gladia_key or os.getenv("GLADIA_API_KEY")
    gladia_key = is_gladia_key_valid(gladia_key)

    model = load_model()

    vad_detector = setup_realtime_vad(args.agent_device.name, model)
    wss_manager = WSSessionManager(STREAMING_CONFIGURATION, gladia_key, silence_timeout_seconds)

    try:
        while True:
            speech_segments = vad_detector.detect_speech()
            is_speaking = bool(speech_segments)
            logger.info(is_speaking)
            #  await wss_manager.manage(is_speaking)
            await asyncio.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        #  await wss_manager.stop_session()
        vad_detector.stop_stream()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass