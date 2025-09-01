class InvalidGladiaKeyException(Exception):
    """Exception raised when the Gladia API key is invalid."""
    def __init__(self, message="The provided Gladia API key is invalid."):
        super().__init__(message)


class DeviceNotFoundException(Exception):
    """Exception raised when the specified USB device is not found."""
    def __init__(self, device: str):
        message = f"The device '{device}' was not found in the USB devices list."
        super().__init__(message)


class AudioStreamStartException(Exception):
    """Exception raised when the audio input stream fails to start."""
    def __init__(self, device_name: str, reason: str = ""):
        message = f"Failed to start audio stream for device '{device_name}'. {reason}"
        super().__init__(message)
        