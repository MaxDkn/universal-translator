# Universal Translator

Real-time audio translation system with automatic voice detection.

## What it does

Captures audio from two sources (headset + phone), transcribes speech, and translates between languages in real-time using Gladia API.

```
Phone ←→ [Translation Hub] ←→ Headset
```

## Quick Start

1. **Install**
   ```bash
   uv sync
   export GLADIA_API_KEY="your-key"
   ```

2. **Find your audio devices**
   ```bash
   uv run python script.py --list-devices
   ```

3. **Run**
   ```bash
   sudo uv run python script.py \
     --agent-device "your-headset" \
     --agent-language fr \
     --phone-device "your-phone-device" \
     --phone-language auto
   ```

## Features

- Auto voice detection (VAD)
- 3s pre-buffer (captures start of speech)
- Real-time translation
- Auto language detection
- 30s silence timeout

## Supported Languages

`auto`, `fr`, `en`, `es`, `de`

## Requirements

- Python 3.8+
- Gladia API key
- USB audio devices
- Linux (tested on Raspberry Pi 5)