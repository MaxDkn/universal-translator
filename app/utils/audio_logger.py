import wave


class AudioLogger:
    """Logger for audio data to WAV files."""
    
    def __init__(self, filename: str, samplerate: int = 16000, channels: int = 1):
        self.filename = filename
        self.samplerate = samplerate
        self.channels = channels
        self.wav_file = wave.open(filename, "wb")
        self.wav_file.setnchannels(channels)
        self.wav_file.setsampwidth(2)  # 16-bit PCM
        self.wav_file.setframerate(samplerate)

    def write(self, data: bytes):
        self.wav_file.writeframes(data)

    def close(self):
        self.wav_file.close()
