# Gladia Real-time Audio Transcription System

Real-time audio transcription system with automatic translation using the Gladia API, voice activity detection (VAD), and text-to-speech synthesis.

## Project Architecture

```
gladia_transcription/
├── main.py                    # Main entry point
├── pyproject.toml             # Project configuration (uv + modern packaging)
├── README.md                  # Documentation
├── config/
│   ├── __init__.py
│   └── settings.py            # Configuration and constants
├── core/
│   ├── __init__.py
│   ├── exceptions.py          # Custom exceptions
│   └── enums.py               # Enums and types
├── audio/
│   ├── __init__.py
│   ├── devices.py             # Audio device management
│   ├── capture.py             # Audio capture
│   ├── playback.py            # Audio playback
│   └── buffers.py             # Shared audio buffers
├── services/
│   ├── __init__.py
│   ├── transcription.py       # Gladia transcription service
│   ├── tts.py                 # Text-to-Speech service
│   └── vad.py                 # Voice Activity Detection (VAD) controller
├── utils/
│   ├── __init__.py
│   ├── logging_utils.py       # Logging utilities
│   └── audio_logger.py        # WAV audio logging
└── managers/
    ├── __init__.py
    └── audio_manager.py       # Main manager
```

## Features

### Main Features

* **Real-time transcription**: Uses the Gladia API for real-time audio transcription
* **Automatic translation**: Bidirectional translation between supported languages
* **Voice Activity Detection (VAD)**: Automatically starts/stops transcription
* **Text-to-Speech (TTS)**: Converts translated text to audio using Edge TTS
* **Pre-buffering**: Captures the last few seconds before speech detection
* **Advanced logging**: Colored logging system with automatic saving

### Technical Features

* **Modular architecture**: Code organized into reusable modules
* **Error handling**: Custom exceptions and robust error management
* **Multi-threading**: Parallel handling of audio, transcription, and TTS
* **Flexible configuration**: Adjustable parameters via arguments and config file
* **Cross-platform support**: Compatible with Windows, Linux, macOS

## Installation

### System Requirements

#### Ubuntu/Debian

```bash
sudo apt-get update
sudo apt-get install portaudio19-dev python3-dev
```

#### CentOS/RHEL

```bash
sudo yum install portaudio-devel python3-devel
```

#### Windows

Install Python 3.8+ and Visual Studio build tools.

### Install with **uv**

```bash
# Clone the project
git clone git@github.com:MaxDkn/universal-translator.git
cd gladia_transcription

# Create and activate environment
uv venv
source .venv/bin/activate  # Linux/Mac
# or
.venv\Scripts\activate     # Windows

# Install dependencies
uv pip install -e .
```

## Configuration

### Environment Variables

```bash
export GLADIA_API_KEY="your_gladia_key_here"
```

### Get a Gladia API Key

1. Create an account on [gladia.io](https://gladia.io)
2. Generate an API key in the dashboard
3. Set the environment variable or use `--gladia-key`

## Usage

### List audio devices

```bash
uv run python main.py --list-devices
```

### Basic usage

```bash
uv run python main.py \
  --gladia-key YOUR_API_KEY \
  --agent-device-id 0 \
  --phone-device-id 1 \
  --agent-language fr \
  --phone-language en
```

### Advanced options

```bash
uv run python main.py \
  --agent-device-id 0 \
  --phone-device-id 1 \
  --agent-language fr \
  --phone-language en \
  --debug \
  --prebuffer-seconds 5.5 \
  --silence-timeout 30
```

### Available Parameters

| Parameter           | Description                  | Default              |
| ------------------- | ---------------------------- | -------------------- |
| `--gladia-key`      | Gladia API key               | Environment variable |
| `--agent-device-id` | Agent microphone device ID   | Required             |
| `--phone-device-id` | Phone microphone device ID   | Required             |
| `--agent-language`  | Language spoken by the agent | `fr`                 |
| `--phone-language`  | Language spoken on the phone | `en`                 |
| `--list-devices`    | List audio devices           | -                    |
| `--debug`           | Enable debug logs            | `false`              |

## Technical Architecture

### Data Flow

1. **Audio capture** → Shared buffer with pre-buffering
2. **VAD** → Speech detection triggers transcription
3. **Transcription** → Gladia API via WebSocket
4. **Translation** → Real-time processing in Gladia
5. **TTS** → Edge TTS for speech synthesis
6. **Playback** → Audio output to device

### Main Components

#### GladiaAudioManager

Main orchestrator coordinating all components.

#### AudioCapture/AudioPlayback

Handles audio capture and playback with PyAudio.

#### VADTranscriptionController

Automatic control based on voice activity detection.

#### TranscriptionAndVoiceService

Interface with Gladia API for real-time transcription/translation.

#### TTSService

Speech synthesis with Microsoft Edge TTS.

## Supported Languages

* **French** (fr) - Voice: fr-FR-DeniseNeural
* **English** (en) - Voice: en-US-AriaNeural
* **Spanish** (es) - Voice: es-ES-AlvaroNeural
* **German** (de) - Voice: de-DE-ConradNeural

## Logs and Debugging

### Log Structure

```
logs/
├── gladia_logs_YYYYMMDD_HHMMSS.txt    # General logs
└── audio/
    ├── input_0_gladia_logs_YYYYMMDD_HHMMSS.wav   # Agent audio
    └── input_1_gladia_logs_YYYYMMDD_HHMMSS.wav   # Phone audio
```

### Log Levels

* **INFO**: Main events
* **DEBUG**: Technical details
* **TRANSLATE**: Transcriptions/translations
* **WARNING**: Warnings
* **ERROR**: Errors

## Development

### Add a new language

1. Add the language in `config/settings.py`
2. Define the corresponding TTS voice
3. Update choices in `main.py`

### Extend functionality

* Inherit from `AudioDevice` for new device types
* Implement `TTSService` for other TTS engines
* Create new VAD controllers with custom logic

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.

## Contribution

Contributions are welcome! Please:

1. Fork the project
2. Create a branch for your feature
3. Commit your changes
4. Push to the branch
5. Open a Pull Request

## Support

For technical support:

* Open an issue in the repository
* Check the Gladia API documentation
* Review debug logs
