import re
import os
import argparse
import requests
import subprocess
from typing import List
from silero_vad import load_silero_vad, read_audio, get_speech_timestamps
import pyaudio
import numpy as np
from collections import deque
import time
import torch


class InvalidGladiaKeyException(Exception):
    """Exception raised when the Gladia API key is invalid."""
    def __init__(self, message="The provided Gladia API key is invalid."):
        super().__init__(message)


class DeviceNotFoundException(Exception):
    """Exception raised when the specified USB device is not found."""
    def __init__(self, device: str):
        message = f"The device '{device}' was not found in the USB devices list."
        super().__init__(message)

def find_audio_device_info(device_name: str) -> dict:
    """Find audio device info by USB device name with fallback options"""
    p = pyaudio.PyAudio()
    
    candidates = []
    
    for i in range(p.get_device_count()):
        try:
            device_info = p.get_device_info_by_index(i)
            if device_info['maxInputChannels'] > 0:  # Input device
                if device_name.lower() in device_info['name'].lower():
                    candidates.append({
                        'index': i,
                        'name': device_info['name'],
                        'sample_rate': int(device_info['defaultSampleRate']),
                        'max_channels': device_info['maxInputChannels']
                    })
        except:
            continue
    
    p.terminate()
    
    if not candidates:
        raise ValueError(f"No input audio device containing '{device_name}' found")
    
    # Return the first candidate or the one with highest sample rate
    return max(candidates, key=lambda x: x['sample_rate'])

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
        self.device_info = find_audio_device_info(device_name)
        self.model = model
        self.chunk_size = chunk_size
        self.audio_buffer = deque()
        
        # Auto-detect best sample rate
        possible_rates = [preferred_sample_rate, 44100, 48000, 22050, 8000]
        self.sample_rate = None
        
        for rate in possible_rates:
            if test_device_configuration(self.device_info['index'], rate):
                self.sample_rate = rate
                break
        
        if self.sample_rate is None:
            self.sample_rate = int(self.device_info['sample_rate'])
            
        print(f"Using device: {self.device_info['name']}")
        print(f"Sample rate: {self.sample_rate} Hz")
        
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
                input_device_index=self.device_info['index'],
                frames_per_buffer=self.chunk_size,
                stream_callback=self._audio_callback
            )
            self.stream.start_stream()
            print("Audio stream started successfully")
            
        except Exception as e:
            print(f"Error starting stream: {e}")
            self.p.terminate()
            raise
        
    def _audio_callback(self, in_data, frame_count, time_info, status):
        try:
            audio_data = np.frombuffer(in_data, dtype=np.int16)
            self.audio_buffer.extend(audio_data)
        except Exception as e:
            print(f"Audio callback error: {e}")
        
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
            print(f"VAD detection error: {e}")
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
    print("\nAvailable audio input devices:")
    
    for i in range(p.get_device_count()):
        try:
            device_info = p.get_device_info_by_index(i)
            if device_info['maxInputChannels'] > 0:
                print(f"  {i}: {device_info['name']} (SR: {device_info['defaultSampleRate']})")
        except:
            continue
    
    p.terminate()

def setup_realtime_vad(device_name: str, model):
    """Setup real-time VAD with error handling"""
    try:
        vad_detector = RobustRealTimeVAD(device_name, model)
        vad_detector.start_stream()
        return vad_detector
    except Exception as e:
        print(f"Failed to setup VAD: {e}")
        return None

def is_devices_exist(device: str) -> str:
    """
    Checks if a specific USB device exists using the `lsusb` command.

    Args:
        device (str): A substring to identify the USB device (e.g., "Logitech", "ID 046d").

    Returns:
        str: The device string if found.

    Raises:
        DeviceNotFoundException: If the device is not found in the `lsusb` output.
        subprocess.SubprocessError: If the `lsusb` command fails to execute.
    """
    regex = r"bus (\d{3}) device (\d{3}): id ([0-9a-f]{4}:[0-9a-f]{0,4}) (.*)"
    try:
        result = subprocess.run(['lsusb'], capture_output=True, text=True, check=True)
        matches = re.finditer(regex, result.stdout, re.MULTILINE | re.IGNORECASE)
        for match in matches:
            if device.lower() in match.group(4).lower():
                return device
        raise DeviceNotFoundException(device)
    except subprocess.SubprocessError as e:
        raise e


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


def setup_realtime_vad(device_name: str, model):
    """Setup real-time VAD with error handling"""
    try:
        vad_detector = RobustRealTimeVAD(device_name, model)
        vad_detector.start_stream()
        return vad_detector
    except Exception as e:
        print(f"Failed to setup VAD: {e}")
        return None


def main(allowed_languages: List[str] = ["auto", "fr", "en", "es", "de"]) -> None:
    """
    Parses command-line arguments for agent and phone devices, languages, and Gladia API key.

    Raises:
        argparse.ArgumentTypeError: If any language or device input is invalid.
        DeviceNotFoundException: If a USB device is not found.
        InvalidGladiaKeyException: If the Gladia API key is invalid.
    """
   
    parser = argparse.ArgumentParser()

    parser.add_argument("-ad", "--agent-device", type=is_devices_exist, help="USB device name for agent mic (e.g., 'Logitech').")
    parser.add_argument("-ag", "--agent-language", choices=allowed_languages, default="auto", help="Language spoken by the agent (default: auto).")
    parser.add_argument("-pd", "--phone-device", type=is_devices_exist, help="USB device name for phone mic.")
    parser.add_argument("-pl", "--phone-language", choices=allowed_languages, default="auto", help="Language spoken by the phone (default: auto).")
    parser.add_argument("-gk", "--gladia-key", type=str, help="Gladia API key. If omitted, will try the 'GLADIA_KEY' environment variable.")

    args = parser.parse_args()

    gladia_key = args.gladia_key or os.getenv("GLADIA_API_KEY")
    gladia_key = is_gladia_key_valid(gladia_key)

    print(args.gladia_key, args.agent_device)
    try:
        device_name = args.agent_device  # Déjà validé par is_devices_exist
        vad_detector = setup_realtime_vad(device_name)
        
        while True:
            speech_segments = vad_detector.detect_speech()
            if speech_segments:
                print(f"Speech detected: {speech_segments}")
                # Process speech segments here
                
    except KeyboardInterrupt:
        vad_detector.stream.stop_stream()
        vad_detector.stream.close()
        vad_detector.p.terminate()

if __name__ == "__main__":
    from silero_vad import load_silero_vad, get_speech_timestamps
    
    # List available devices first
    list_audio_devices()
    
    # Load VAD model
    model = load_silero_vad()
    
    # Setup with your device
    device_name = "CM477-30757"  # Replace with actual device
    vad_detector = setup_realtime_vad(device_name, model)
    
    if vad_detector:
        try:
            print("Listening for speech... Press Ctrl+C to stop")
            
            while True:
                speech_segments = vad_detector.detect_speech()
                if speech_segments:
                    print(f"Speech detected: {speech_segments}")
                
                time.sleep(0.1)  # Small delay
                
        except KeyboardInterrupt:
            print("Stopping...")
        finally:
            vad_detector.stop_stream()
