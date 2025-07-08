import os
import io
import json
import time
import queue
import base64
import asyncio
import logging
import warnings
import argparse
import threading
from enum import Enum
from collections import deque
from datetime import datetime, timedelta
from typing import Literal, TypedDict, List, Optional

import torch
import pyaudio
import requests
import edge_tts
import numpy as np
from pydub import AudioSegment
from websockets.exceptions import ConnectionClosedOK
from websockets.asyncio.client import ClientConnection, connect
from silero_vad import get_speech_timestamps, load_silero_vad
warnings.filterwarnings("ignore", message="Sampling rate is a multiply of 16000")


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

class TranslationConfiguration(TypedDict):
    target_languages: list[str]
    context_adaptation: bool
    context: str

class RealtimeProcessingConfiguration(TypedDict):
    translation: bool
    translation_config: TranslationConfiguration | None

class StreamingConfiguration(TypedDict):
    encoding: Literal["wav/pcm", "wav/alaw", "wav/ulaw"]
    bit_depth: Literal[8, 16, 24, 32]
    sample_rate: Literal[8_000, 16_000, 32_000, 44_100, 48_000]
    channels: int
    language_config: LanguageConfiguration | None
    realtime_processing: RealtimeProcessingConfiguration | None


class AudioDevice:
    def __init__(self, index: int, name: str, sample_rate: int, max_input_channels: int, max_output_channels: int):
        self.index = index
        self.name = name
        self.sample_rate = sample_rate
        self.max_input_channels = max_input_channels
        self.max_output_channels = max_output_channels

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"AudioDevice(name='{self.name}', index={self.index}, sample_rate={self.sample_rate}, max_input_channels={self.max_input_channels}, max_output_channels={self.max_output_channels})"


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

class SharedAudioBuffer:
    def __init__(self, max_chunks: int = 1000, prebuffer_seconds: float = 3.0):
        self.buffer = deque(maxlen=max_chunks)
        self.lock = threading.Lock()
        self.is_recording = False
        self.listeners = []

        self.prebuffer_seconds = prebuffer_seconds
        self.prebuffer_duration = timedelta(seconds=prebuffer_seconds)
        
    def start_recording(self):
        with self.lock:
            self.is_recording = True
            
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
        """
        Retrieve pre-buffer chunks (last X seconds).

        Returns:
            list: List of chunk info dictionaries within the pre-buffer duration.
        """
        with self.lock:
            if not self.buffer:
                return []
                
            current_time = datetime.now()
            cutoff_time = current_time - self.prebuffer_duration
            
            prebuffer_chunks = []
            for chunk_info in reversed(self.buffer):
                if chunk_info['timestamp'] >= cutoff_time:
                    prebuffer_chunks.append(chunk_info)
                else:
                    break
                    
            return list(reversed(prebuffer_chunks))
            
    def register_listener(self) -> queue.Queue:
        listener_queue = queue.Queue(maxsize=100)
        self.listeners.append(listener_queue)
        return listener_queue
        
    def unregister_listener(self, listener_queue: queue.Queue):
        if listener_queue in self.listeners:
            self.listeners.remove(listener_queue)

class SharedAudioPlaybackBuffer:
    def __init__(self, max_chunks: int = 1000):
        self.buffer = deque(maxlen=max_chunks)
        self.lock = threading.Lock()
        self.is_playing = False
        
    def add_audio_chunk(self, chunk_data: bytes):
        with self.lock:
            self.buffer.append(chunk_data)
            
    def get_chunks_to_play(self) -> list:
        with self.lock:
            if not self.buffer:
                return []
            chunks = list(self.buffer)
            self.buffer.clear()
            return chunks
            
    def clear(self):
        with self.lock:
            self.buffer.clear()

class AudioCapture:
    def __init__(self, buffer: SharedAudioBuffer, device: AudioDevice):
        self.buffer = buffer
        self.device = device
        self.p = pyaudio.PyAudio()
        self.stream = None
        self.is_running = False
        self.thread = None
        
        self.CHANNELS = 1
        self.FORMAT = pyaudio.paInt16
        self.FRAMES_PER_BUFFER = 3200
        
        if self.device:
            self.SAMPLE_RATE = self._find_best_sample_rate_for_device()
            logger.info(f"Using device: {self.device.name}")
            logger.info(f"Device default rate: {self.device.sample_rate} Hz")
            logger.info(f"Selected rate: {self.SAMPLE_RATE} Hz")
        else:
            self.SAMPLE_RATE = self._find_best_sample_rate_auto()
            logger.warning("No specific device provided, using auto-detection")
        
    def _find_best_sample_rate_for_device(self):
        """
        Find the best sample rate for the specified device.

        Returns:
            int: Best supported sample rate for the device.
        """
        vad_compatible_rates = [16000, 8000, 32000, 48000]
        standard_rates = [44100, 22050, 11025]
        
        device_rate = int(self.device.sample_rate)
        if device_rate in vad_compatible_rates:
            if self._test_sample_rate(device_rate, self.device.index):
                logger.info(f"Using device default rate (VAD-compatible): {device_rate} Hz")
                return device_rate
        
        for rate in vad_compatible_rates:
            if self._test_sample_rate(rate, self.device.index):
                logger.info(f"Using VAD-compatible rate: {rate} Hz")
                return rate
        
        if self._test_sample_rate(device_rate, self.device.index):
            logger.warning(f"Using device default rate (non-VAD): {device_rate} Hz (will need resampling)")
            return device_rate
        
        for rate in standard_rates:
            if self._test_sample_rate(rate, self.device.index):
                logger.warning(f"Using standard rate: {rate} Hz (will need resampling)")
                return rate
                
        logger.error(f"No supported sample rate found for device {self.device.name}")
        return 16000
    
    def _find_best_sample_rate_auto(self):
        """
        Automatically find a sample rate (fallback).

        Returns:
            int: Best supported sample rate for auto-detected device.
        """
        vad_compatible_rates = [16000, 8000, 32000, 48000]
        standard_rates = [44100, 22050]
        
        try:
            default_device = self.p.get_default_input_device_info()
            device_index = default_device['index']
        except:
            device_index = self._get_first_input_device()
        
        for rate in vad_compatible_rates:
            if self._test_sample_rate(rate, device_index):
                logger.info(f"Auto-detected VAD-compatible rate: {rate} Hz")
                return rate
        
        for rate in standard_rates:
            if self._test_sample_rate(rate, device_index):
                logger.warning(f"Auto-detected standard rate: {rate} Hz (will need resampling)")
                return rate
                
        return 16000
    
    def _test_sample_rate(self, rate, device_index):
        """
        Test if a sample rate is supported by the device.

        Args:
            rate (int): Sample rate to test.
            device_index (int): Device index to test.

        Returns:
            bool: True if the sample rate is supported, False otherwise.
        """
        try:
            return self.p.is_format_supported(
                rate=rate,
                input_device=device_index,
                input_channels=self.CHANNELS,
                input_format=self.FORMAT
            )
        except Exception as e:
            logger.debug(f"Sample rate {rate} test failed on device {device_index}: {e}")
            return False
    
    def _get_first_input_device(self):
        """
        Find the first available input device.

        Returns:
            int: Index of the first available input device.

        Raises:
            Exception: If no input device is found.
        """
        for i in range(self.p.get_device_count()):
            try:
                info = self.p.get_device_info_by_index(i)
                if info['maxInputChannels'] > 0:
                    return i
            except:
                continue
        raise Exception("No input device found")
    
    def start_capture(self):
        if self.is_running:
            return
            
        self.is_running = True
        
        config = {
            'format': self.FORMAT,
            'channels': self.CHANNELS,
            'rate': self.SAMPLE_RATE,
            'input': True,
            'frames_per_buffer': self.FRAMES_PER_BUFFER,
        }
        
        if self.device:
            config['input_device_index'] = self.device.index
            
        try:
            logger.debug(f"Opening audio stream: {config}")
            self.stream = self.p.open(**config)
            device_name = self.device.name if self.device else "default"
            logger.info(f"✓ Audio stream opened successfully")
            logger.info(f"  Device: {device_name}")
            logger.info(f"  Sample rate: {self.SAMPLE_RATE} Hz")
            logger.info(f"  Channels: {self.CHANNELS}")
            
        except Exception as e:
            device_name = self.device.name if self.device else "auto-detect"
            logger.error(f"Failed to open audio stream for device '{device_name}': {e}")
            
            if self.device:
                logger.warning("Retrying without specific device...")
                try:
                    config.pop('input_device_index', None)
                    self.stream = self.p.open(**config)
                    logger.warning("✓ Opened with default device")
                except Exception as e2:
                    raise AudioStreamStartException(device_name, str(e2))
            else:
                raise AudioStreamStartException(device_name, str(e))
        
        self.thread = threading.Thread(target=self._capture_loop)
        self.thread.daemon = True
        self.thread.start()
        
    def stop_capture(self):
        self.is_running = False
        
        if self.thread:
            self.thread.join(timeout=2)
            
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception as e:
                logger.warning(f"Error closing stream: {e}")
            
    def _capture_loop(self):
        while self.is_running:
            try:
                if self.stream is None:
                    logger.error("Stream audio non initialisé")
                    break
                
                data = self.stream.read(
                    self.FRAMES_PER_BUFFER, 
                    exception_on_overflow=False
                )
                self.buffer.add_chunk(data)
                time.sleep(0.01)
            except Exception as e:
                logger.error(f"Erreur capture audio: {e}")
                break
                
    def cleanup(self):
        self.stop_capture()
        try:
            self.p.terminate()
        except Exception as e:
            logger.warning(f"Error terminating PyAudio: {e}")

class AudioPlayback:
    def __init__(self, buffer: SharedAudioPlaybackBuffer, device: AudioDevice):
        self.buffer = buffer
        self.device = device
        self.p = pyaudio.PyAudio()
        self.stream = None
        self.is_playing = False
        self.thread = None
        
        self.CHANNELS = 1
        self.FORMAT = pyaudio.paInt16
        self.FRAMES_PER_BUFFER = 512
        
        self.SAMPLE_RATE = self._find_best_output_sample_rate()
        logger.info(f"Using output device: {self.device.name}")
        logger.info(f"Selected output sample rate: {self.SAMPLE_RATE} Hz")
    
    def _find_best_output_sample_rate(self):
        """
        Find the best sample rate supported by the output device.

        Returns:
            int: Best supported sample rate for the output device.
        """
        preferred_rates = [22050, 44100, 48000, 16000, 24000, 32000, 8000]
        
        device_rate = int(self.device.sample_rate)
        if self._test_output_sample_rate(device_rate, self.device.index):
            logger.debug(f"Device default rate {device_rate} Hz is supported")
            if device_rate in preferred_rates or device_rate > 8000:
                return device_rate
        
        for rate in preferred_rates:
            if self._test_output_sample_rate(rate, self.device.index):
                logger.debug(f"Found supported rate: {rate} Hz")
                return rate
        
        logger.warning(f"No preferred sample rate found, using device default: {device_rate} Hz")
        return device_rate
    
    def _find_best_output_sample_rate_auto(self):
        """
        Automatically find a sample rate for the default device.

        Returns:
            int: Best supported sample rate for the default output device.
        """
        preferred_rates = [22050, 44100, 48000, 16000, 24000, 32000]
        
        try:
            default_device = self.p.get_default_output_device_info()
            device_index = default_device['index']
            device_rate = int(default_device['defaultSampleRate'])
            
            if self._test_output_sample_rate(device_rate, device_index):
                return device_rate
        except:
            device_index = self._get_first_output_device()
        
        for rate in preferred_rates:
            if self._test_output_sample_rate(rate, device_index):
                return rate
        
        return 44100
    
    def _get_first_output_device(self):
        """
        Find the first available output device.

        Returns:
            int: Index of the first available output device.

        Raises:
            Exception: If no output device is found.
        """
        for i in range(self.p.get_device_count()):
            try:
                info = self.p.get_device_info_by_index(i)
                if info['maxOutputChannels'] > 0:
                    return i
            except:
                continue
        raise Exception("No output device found")
    
    def _test_output_sample_rate(self, rate, device_index):
        """
        Test if a sample rate is supported by the output device.

        Args:
            rate (int): Sample rate to test.
            device_index (int): Output device index to test.

        Returns:
            bool: True if the sample rate is supported, False otherwise.
        """
        try:
            return self.p.is_format_supported(
                rate=rate,
                output_device=device_index,
                output_channels=self.CHANNELS,
                output_format=self.FORMAT
            )
        except Exception as e:
            logger.debug(f"Sample rate {rate} test failed on output device {device_index}: {e}")
            return False
        
    def start_playback(self):
        if self.is_playing:
            return
            
        self.is_playing = True
        
        config = {
            'format': self.FORMAT,
            'channels': 1,
            'rate': self.SAMPLE_RATE,
            'output': True,
            'frames_per_buffer': self.FRAMES_PER_BUFFER,
        }
        
        if self.device:
            config['output_device_index'] = self.device.index
            
        try:
            self.stream = self.p.open(**config)
            device_name = self.device.name if self.device else "default"
            logger.info(f"✓ Audio playback stream opened successfully on {device_name}")
            
        except Exception as e:
            device_name = self.device.name if self.device else "auto-detect"
            logger.error(f"Failed to open audio playback stream for device '{device_name}': {e}")
            raise AudioStreamStartException(device_name, str(e))
        
        self.thread = threading.Thread(target=self._playback_loop)
        self.thread.daemon = True
        self.thread.start()
        
    def stop_playback(self):
        self.is_playing = False
        
        if self.thread:
            self.thread.join(timeout=2)
            
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception as e:
                logger.warning(f"Error closing playback stream: {e}")
                
    def _playback_loop(self):
        while self.is_playing:
            try:
                if self.stream is None:
                    logger.error("Playback stream not initialized")
                    break
                
                chunks = self.buffer.get_chunks_to_play()
                if chunks:
                    for chunk_data in chunks:
                        if not self.is_playing:
                            break
                        self.stream.write(chunk_data)
                        
                time.sleep(0.01)
            except Exception as e:
                logger.error(f"Error in playback loop: {e}")
                break
                
    def cleanup(self):
        self.stop_playback()
        try:
            self.p.terminate()
        except Exception as e:
            logger.warning(f"Error terminating PyAudio playback: {e}")

class TTSService:
    def __init__(self, playback_audio: AudioPlayback, voice: str = "fr-FR-DenisNeural"):
        self.playback_audio = playback_audio
        self.playback_buffer = playback_audio.buffer
        self.voice = voice
        
    async def synthesize_and_queue(self, text: str):
        """
        Synthesize text and add it to the playback buffer.

        Args:
            text (str): Text to synthesize.
        """
        if not text.strip():
            return
            
        logger.debug(f"TTS synthesis [{self.voice}]: {text[:50]}...")
        
        try:
            communicate = edge_tts.Communicate(text=text, voice=self.voice)
            
            audio_data = b""
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_data += chunk["data"]
            
            if not audio_data:
                logger.error("No audio received from Edge TTS")
                return
                
            converted_audio = self._convert_audio_format(audio_data)
            if converted_audio:
                self._queue_audio_chunks(converted_audio)
            else:
                logger.error("Audio conversion failed")
                
        except Exception as e:
            logger.error(f"Error in TTS synthesis: {e}")
    
    def _convert_audio_format(self, audio_data: bytes) -> bytes:
        """
        Convert audio to PyAudio format.

        Args:
            audio_data (bytes): Raw audio data to convert.

        Returns:
            bytes: Converted audio data in PyAudio format.
        """
        try:
            audio_segment = AudioSegment.from_mp3(io.BytesIO(audio_data))
            
            audio_segment = audio_segment.set_frame_rate(self.playback_audio.SAMPLE_RATE)
            audio_segment = audio_segment.set_channels(1)
            audio_segment = audio_segment.set_sample_width(2)
            
            return audio_segment.raw_data
            
        except Exception as e:
            logger.error(f"Audio conversion error: {e}")
            return b''
    
    def _queue_audio_chunks(self, audio_data: bytes, chunk_size: int = 1024):
        """
        Split and send audio to the playback buffer.

        Args:
            audio_data (bytes): Audio data to split and queue.
            chunk_size (int): Size of each audio chunk. Defaults to 1024.
        """
        try:
            for i in range(0, len(audio_data), chunk_size):
                chunk = audio_data[i:i + chunk_size]
                if len(chunk) > 0:
                    self.playback_buffer.add_audio_chunk(chunk)
        except Exception as e:
            logger.error(f"Error queueing audio chunks: {e}")


class TranscriptionAndVoiceService:
    def __init__(self, gladia_key: str, audio_capture: AudioCapture, tts_service: TTSService, 
                 agent_language: str = "fr", target_language: str = "en"):

        self.audio_capture = audio_capture
        self.gladia_key = gladia_key
        self.tts_service = tts_service

        self.agent_language = agent_language
        self.target_language = target_language

        
        self.is_traduction = (
            self.target_language and self.target_language != "auto" and agent_language != target_language
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
        response = requests.post(
            "https://api.gladia.io/v2/live",
            headers={"X-Gladia-Key": self.gladia_key},
            json=self.STREAMING_CONFIGURATION,
            timeout=3,
        )
        if not response.ok:
            logger.error(f"{response.status_code}: {response.text or response.reason}")
            exit(response.status_code)
        return response.json()
        
    async def print_messages_from_socket(self, socket: ClientConnection) -> None:
        
        async for message in socket:
            if self.stop_event.is_set():
                break
                
            content = json.loads(message)
            if self.is_transcribing and content["type"] == "translation":
                text = content["data"]["translated_utterance"]["text"].strip()
                print(" "*4, text)
                
                if self.tts_service and text:
                    asyncio.create_task(self.tts_service.synthesize_and_queue(text))
                    
            elif content["type"] == "transcription":
                text = content["data"]["translated_utterance"]["text"].strip()
                print(" "*4, text)
            
            
            if content["type"] == "post_final_transcript":
                logger.debug("Transcription finished automatically")
                self.is_transcribing = False
                break
    
    async def send_prebuffer_chunks(self, socket: ClientConnection) -> None:
        """
        Send pre-buffer chunks at the beginning of transcription.

        Args:
            socket (ClientConnection): WebSocket connection to send chunks to.
        """
        prebuffer_chunks = self.audio_capture.buffer.get_prebuffer_chunks()
        
        if prebuffer_chunks:
            logger.debug(f"Sending {len(prebuffer_chunks)} pre-buffer chunks ({self.audio_capture.buffer.prebuffer_seconds}s)")
            
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
        logger.debug("Stopping transcription requested")
        self.stop_event.set()
        self.is_transcribing = False

class VADTranscriptionController:
    def __init__(self, audio_capture: AudioCapture, transcription_service: TranscriptionAndVoiceService, vad_model, silence_timeout: float = 30.0):
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
    voice_edge_tts = {
        "fr": "fr-FR-DenisNeural",
        "en": "en-US-AriaNeural",
        "es": "es-ES-AlvaroNeural",
        "de": "de-DE-ConradNeural"
    }

    def __init__(self, 
                 gladia_key: str,

                 agent_device: AudioDevice,
                 phone_device: AudioDevice,
                 agent_language: str = "fr",
                 phone_language: str = "en",
                 
                 silence_timeout: float = 30.0, 
                 prebuffer_seconds: float = 5.5):

        self.audio_buffer = SharedAudioBuffer(prebuffer_seconds=prebuffer_seconds)
        self.audio_capture = AudioCapture(self.audio_buffer, agent_device)
                
        self.playback_buffer = SharedAudioPlaybackBuffer()
        self.audio_playback = AudioPlayback(self.playback_buffer, phone_device)
        
        voice = self.voice_edge_tts.get(phone_language, "en-US-AriaNeural")
        
        self.tts_service = TTSService(self.audio_playback, voice=voice)
        logger.debug(f"TTS enabled with voice: {voice}")
        
        self.transcription_service = TranscriptionAndVoiceService(
            gladia_key=gladia_key, 
            audio_capture=self.audio_capture,
            tts_service=self.tts_service,
            agent_language=agent_language, 
            target_language=phone_language,
        )
        
        self.vad_controller = VADTranscriptionController(
            audio_capture=self.audio_capture, 
            transcription_service=self.transcription_service, 
            vad_model=load_silero_vad(),
            silence_timeout=silence_timeout
        )
        
    def start_audio_capture(self):
        logger.debug(f"Starting audio capture with {self.audio_buffer.prebuffer_seconds}s pre-buffer...")
        self.audio_capture.start_capture()
        
        if self.audio_playback:
            self.audio_playback.start_playback()
        return self
        
    def stop_audio_capture(self):
        logger.debug("Stopping audio capture...")
        self.audio_capture.stop_capture()
        
        if self.audio_playback:
            self.audio_playback.stop_playback()
        
    def start_vad_monitoring(self):
        self.vad_controller.start_monitoring()
        return self
        
    def stop_vad_monitoring(self):
        self.vad_controller.stop_monitoring()
        
    def get_buffer_stats(self):
        stats = {
            'total_chunks': len(self.audio_buffer.get_all_chunks()),
            'prebuffer_chunks': len(self.audio_buffer.get_prebuffer_chunks()),
            'prebuffer_seconds': self.audio_buffer.prebuffer_seconds,
            'is_recording': self.audio_buffer.is_recording,
            'listeners': len(self.audio_buffer.listeners)
        }
        
        if self.playback_buffer:
            stats['playback_buffer_size'] = len(self.playback_buffer.buffer)
            
        return stats
        
    def get_vad_status(self):
        return self.vad_controller.get_status()
        
    def cleanup(self):
        self.vad_controller.stop_monitoring()
        self.audio_capture.cleanup()
        
        if self.audio_playback:
            self.audio_playback.cleanup()

def get_all_audio_devices(filter: list[str] = ["default", "dmix", "to_headset", "from_pc", "dmix_combined", "spdif", "iec958",
                                           "both_outputs", "vdownmix", "upmix", "speex", "speexrate", "samplerate", "lavrate",
                                           "surround40", "front", "pulse", "sysdefault", "a52"]):
    """
    Scans and returns available audio input and output devices.

    Args:
        filter (list[str]): List of device names to filter out.

    Returns:
        tuple: (input_devices, output_devices)
            - input_devices: list of AudioDevice objects with input capabilities
            - output_devices: list of AudioDevice objects with output capabilities

    Each AudioDevice contains:
        - index (int): device index in PyAudio
        - name (str): device name
        - sample_rate (int): default sample rate
        - max_input_channels (int): number of input channels
        - max_output_channels (int): number of output channels
    """
    p = pyaudio.PyAudio()
    input_devices = []
    output_devices = []

    try:
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)

            if int(info['maxInputChannels']) > 0 and str(info['name']) not in filter:
                device = AudioDevice(
                    index=i,
                    name=str(info['name']),
                    sample_rate=int(info['defaultSampleRate']),
                    max_input_channels=int(info['maxInputChannels']),
                    max_output_channels=int(info['maxOutputChannels'])
                )
                input_devices.append(device)
                logger.debug(f"Detected input device: {device}")

            if int(info['maxOutputChannels']) > 0 and str(info['name']) not in filter:
                device = AudioDevice(
                    index=i,
                    name=str(info['name']),
                    sample_rate=int(info['defaultSampleRate']),
                    max_input_channels=int(info['maxInputChannels']),
                    max_output_channels=int(info['maxOutputChannels'])
                )
                output_devices.append(device)
                logger.debug(f"Detected output device: {device}")
    
    except Exception as e:
        logger.error(f"Error while retrieving audio devices: {e}")

    finally:
        p.terminate()

    if not input_devices:
        logger.warning("No input devices found.")
    if not output_devices:
        logger.warning("No output devices found.")

    return input_devices, output_devices

def get_device_info(device_name: str) -> AudioDevice:
    """
    Find audio device info by USB device name with fallback options.

    Args:
        device_name (str): USB device name to search for.

    Returns:
        AudioDevice: Audio device with the highest sample rate among candidates.

    Raises:
        DeviceNotFoundException: If no matching device is found.
    """
    p = pyaudio.PyAudio()
    
    candidates = []
    
    for i in range(p.get_device_count()):
        try:
            device_info = p.get_device_info_by_index(i)
            if int(device_info['maxInputChannels']) > 0:
                if device_name.lower() in str(device_info['name']).lower():
                    candidates.append(AudioDevice(
                        index=i,
                        name=str(device_info['name']),
                        sample_rate=int(device_info['defaultSampleRate']),
                        max_input_channels=int(device_info['maxInputChannels']),
                        max_output_channels=int(device_info['maxOutputChannels'])
                    ))
        except Exception:
            continue
    
    p.terminate()
    
    if not candidates:
        raise DeviceNotFoundException(device_name)
    
    return max(candidates, key=lambda x: x.sample_rate)

def list_audio_devices():
    """
    Logs the list of available audio input and output devices.

    This function retrieves and logs the available input (microphones) and output 
    (speakers) audio devices, including their index, name, sample rate, and number 
    of channels.

    Requires a `get_all_audio_devices()` function that returns a tuple:
    (list of input devices, list of output devices). Each device should have
    `index`, `name`, `sample_rate`, `max_input_channels`, and `max_output_channels` attributes.
    """
    input_devices, output_devices = get_all_audio_devices()
    
    logger.info("===== INPUT DEVICES (MICROPHONES) =====")
    if not input_devices:
        logger.warning("No input devices found.")
    else:
        for device in input_devices:
            logger.info(f"[{device.index:2d}] {device.name}")
            logger.info(f"     Sample rate: {device.sample_rate} Hz, Input channels: {device.max_input_channels}")
    
    logger.info("====== OUTPUT DEVICES (SPEAKERS) ======")
    if not output_devices:
        logger.warning("No output devices found.")
    else:
        for device in output_devices:
            logger.info(f"[{device.index:2d}] {device.name}")
            logger.info(f"     Sample rate: {device.sample_rate} Hz, Output channels: {device.max_output_channels}")

async def main(allowed_languages: List[str] = ["fr", "en", "es", "de"], 
               silence_timeout: float = 15.0, 
               prebuffer_seconds: float = 5.5, 
               debug: bool = True):
    if debug:
        logger.setLevel(logging.DEBUG)

    parser = argparse.ArgumentParser()

    parser.add_argument("--agent-device",   type=get_device_info, help="USB device name for agent mic (e.g., 'CM477').")
    parser.add_argument("--agent-language", choices=allowed_languages, default="fr", help="Language spoken by the agent.")
    parser.add_argument("--phone-device",   type=get_device_info, help="USB device name for phone mic.")
    parser.add_argument("--phone-language", choices=allowed_languages, default="en", help="Language spoken by the phone.")
    
    parser.add_argument("--gladia-key", type=str, help="Gladia API key. If omitted, will try the 'GLADIA_KEY' environment variable.")
    parser.add_argument("--list-devices", action="store_true", help="List all available audio devices and exit.")
    
    args = parser.parse_args()
    
    if args.list_devices:
        list_audio_devices()
        return
    else:
        if not args.agent_device or not args.phone_device:
            logger.error(f"--agent-device ({args.agent_device}) and --phone-device ({args.phone_device}) cannot be empty.")
            return

    gladia_key = args.gladia_key or os.getenv("GLADIA_API_KEY")
    gladia_key = is_gladia_key_valid(gladia_key)
        
    print(f"===== Gladia Live Transcription + Auto VAD + Pre-buffer ({prebuffer_seconds}s) =====")
    
    manager = GladiaAudioManager(
        gladia_key, 

        agent_device=args.agent_device,
        phone_device=args.phone_device,
        agent_language=args.agent_language, 
        phone_language=args.phone_language,
        
        silence_timeout=silence_timeout, 
        prebuffer_seconds=prebuffer_seconds,
    ).start_audio_capture().start_vad_monitoring()
    
    logger.info('Starting transcription...\n')
    logger.info(f"TTS enabled - Output device: {args.phone_device.name}")

    try:
        while True:
            if input().lower() in ['q', '']:
                break
            await asyncio.sleep(0.1)
    except KeyboardInterrupt:
        logger.info("Program interrupted by user.")
    finally:
        manager.cleanup()
        logger.info("Goodbye!")


if __name__ == "__main__":
    asyncio.run(main())
