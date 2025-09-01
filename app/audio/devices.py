import pyaudio
import logging
from core.exceptions import DeviceNotFoundException

logger = logging.getLogger(__name__)


class AudioDevice:
    """Represents an audio device with its properties."""
    
    def __init__(self, index: int, name: str, sample_rate: int, 
                 max_input_channels: int, max_output_channels: int):
        self.index = index
        self.name = name
        self.sample_rate = sample_rate
        self.max_input_channels = max_input_channels
        self.max_output_channels = max_output_channels

    def __str__(self):
        return self.name

    def __repr__(self):
        return (f"AudioDevice(name='{self.name}', index={self.index}, "
               f"sample_rate={self.sample_rate}, "
               f"max_input_channels={self.max_input_channels}, "
               f"max_output_channels={self.max_output_channels})")


def get_all_audio_devices(filter_list: list[str] = None) -> list:
    """
    Scans and returns available audio input and output devices.
    """
    if filter_list is None:
        filter_list = ["dmix", "to_headset", "from_pc", "dmix_combined", 
                      "spdif", "iec958", "both_outputs", "vdownmix", "upmix", 
                      "speex", "speexrate", "samplerate", "lavrate", 
                      "surround40", "front", "sysdefault", "a52"]
    
    p = pyaudio.PyAudio()
    input_devices = []
    output_devices = []
    
    try:
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            
            if int(info['maxInputChannels']) > 0 and str(info['name']) not in filter_list:
                device = AudioDevice(
                    index=i,
                    name=str(info['name']),
                    sample_rate=int(info['defaultSampleRate']),
                    max_input_channels=int(info['maxInputChannels']),
                    max_output_channels=int(info['maxOutputChannels'])
                )
                input_devices.append(device)
                logger.debug(f"Detected input device: {device}")

            if int(info['maxOutputChannels']) > 0 and str(info['name']) not in filter_list:
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


def get_device_info(device_name: str, index: int = None) -> AudioDevice:
    """
    Find audio device info by USB device name with fallback options.
    """
    p = pyaudio.PyAudio()
    
    if index is not None:
        device_info = p.get_device_info_by_index(index)
        return AudioDevice(
            index=index,
            name=str(device_info['name']),
            sample_rate=int(device_info['defaultSampleRate']),
            max_input_channels=int(device_info['maxInputChannels']),
            max_output_channels=int(device_info['maxOutputChannels'])
        )

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
    """Logs the list of available audio input and output devices."""
    input_devices, output_devices = get_all_audio_devices()
    
    logger.info("===== INPUT DEVICES (MICROPHONES) =====")
    if not input_devices:
        logger.warning("No input devices found.")
    else:
        for device in input_devices:
            logger.info(f"[{device.index:2d}] {device.name}")
            logger.info(f"    Sample rate: {device.sample_rate} Hz, "
                       f"Input channels: {device.max_input_channels}")

    logger.info("====== OUTPUT DEVICES (SPEAKERS) ======")
    if not output_devices:
        logger.warning("No output devices found.")
    else:
        for device in output_devices:
            logger.info(f"[{device.index:2d}] {device.name}")
            logger.info(f"    Sample rate: {device.sample_rate} Hz, "
                       f"Output channels: {device.max_output_channels}")
