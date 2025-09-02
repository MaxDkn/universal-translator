import time
import threading
import pyaudio
import logging
from datetime import datetime

from .devices import AudioDevice
from ..utils.audio_logger import AudioLogger
from ..core.exceptions import AudioStreamStartException
from .buffers import SharedAudioBuffer, SharedAudioPlaybackBuffer

logger = logging.getLogger(__name__)


class AudioCapture:
    """Handles audio capture from a specified device."""
    
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
        self.SAMPLE_RATE = self._find_best_sample_rate_for_device()
        
        logger.debug(f"Using device: {self.device.name}")
        logger.debug(f"Device default rate: {self.device.sample_rate} Hz")
        logger.debug(f"Selected rate: {self.SAMPLE_RATE} Hz")
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        filename = f"{timestamp} input-{device.index}.wav"
        logger.info("AudioLogger created")
        self.audio_logger = AudioLogger(filename, samplerate=device.sample_rate, channels=1)

    def _find_best_sample_rate_for_device(self):
        """Find the best sample rate for the specified device."""
        vad_compatible_rates = [16000, 8000, 32000, 48000]
        standard_rates = [44100, 22050, 11025]
        device_rate = int(self.device.sample_rate)

        if device_rate in vad_compatible_rates:
            if self._test_sample_rate(device_rate, self.device.index):
                logger.debug(f"Using device default rate (VAD-compatible): {device_rate} Hz")
                return device_rate

        for rate in vad_compatible_rates:
            if self._test_sample_rate(rate, self.device.index):
                logger.debug(f"Using VAD-compatible rate: {rate} Hz")
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

    def _test_sample_rate(self, rate, device_index):
        """Test if a sample rate is supported by the device."""
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
            logger.debug("Audio stream opened successfully")
            logger.debug(f"Device: {device_name}")
            logger.debug(f"Sample rate: {self.SAMPLE_RATE} Hz")
            logger.debug(f"Channels: {self.CHANNELS}")
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
                self.audio_logger.write(data)
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
        logger.debug(f"Using output device: {self.device.name}")
        logger.debug(f"Selected output sample rate: {self.SAMPLE_RATE} Hz")
    
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
            logger.debug(f"Audio playback stream opened successfully on {device_name}")
            
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
