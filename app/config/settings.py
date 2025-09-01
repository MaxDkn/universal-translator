"""Configuration settings for the Gladia transcription system."""

# Audio Configuration
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHANNELS = 1
DEFAULT_FORMAT = "paInt16"  # pyaudio format
DEFAULT_FRAMES_PER_BUFFER = 3200
DEFAULT_CHUNK_SIZE = 1024

# VAD Configuration
VAD_COMPATIBLE_RATES = [16000, 8000, 32000, 48000]
STANDARD_RATES = [44100, 22050, 11025]
VAD_MIN_SPEECH_DURATION_MS = 200
VAD_MIN_SILENCE_DURATION_MS = 100
VAD_WINDOW_SIZE_SAMPLES = 1536
VAD_SPEECH_PAD_MS = 30

# Buffer Configuration
DEFAULT_MAX_CHUNKS = 1000
DEFAULT_PREBUFFER_SECONDS = 3.0
DEFAULT_SILENCE_TIMEOUT = 30.0

# TTS Configuration
TTS_VOICES = {
    "fr": "fr-FR-DeniseNeural",
    "en": "en-US-AriaNeural",
    "es": "es-ES-AlvaroNeural",
    "de": "de-DE-ConradNeural"
}

# Audio device filter list
AUDIO_DEVICE_FILTER = [
    "dmix", "to_headset", "from_pc", "dmix_combined", 
    "spdif", "iec958", "both_outputs", "vdownmix", "upmix", 
    "speex", "speexrate", "samplerate", "lavrate", 
    "surround40", "front", "sysdefault", "a52"
]

# API Configuration
GLADIA_API_BASE_URL = "https://api.gladia.io"
GLADIA_LIVE_ENDPOINT = f"{GLADIA_API_BASE_URL}/v2/live"
GLADIA_PRERECORDED_ENDPOINT = f"{GLADIA_API_BASE_URL}/v2/pre-recorded"

# Translation Configuration
DEFAULT_TRANSLATION_CONTEXT = "This is a conversation in a Call center for Gladia product"

# Logging Configuration
LOG_FORMAT = "%(levelname)-9s %(asctime)s - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Supported languages
SUPPORTED_LANGUAGES = ["fr", "en", "es", "de"]