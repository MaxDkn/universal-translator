# Universal Transcriptor

A professional translation interface with real-time transcription and logs monitoring.

## 🚀 Quick Start

### 1. Install Dependencies
```bash
uv sync
```

### 2. Launch Server
```bash
uv run script.py
```

### 3. Launch Chromium (Kiosk Mode)
```bash
chromium-browser --app=http://127.0.0.1:8000 --start-fullscreen --disable-gpu --disable-software-rasterizer
```

That's it! Your application is running.

## 📱 Interface

### Main Menu
- **Settings** : Configure languages and audio devices
- **Launch** : Start the transcription service

### Settings Page
- **Agent Language** : Input language (what the agent listens to)
- **Output Language** : Translation target language
- **Agent Device** : Input audio device
- **Output Device** : Output audio device

### Launch Page
- **TEXT View** : Live transcription with source audio and translation
- **LOGS View** : Real-time system logs with color coding
- **STOP Button** : Stops service and returns to main menu

## 📁 File Structure

```
.
├── main.py                 # FastAPI server
├── templates/
│   ├── welcome.html        # Main menu
│   ├── settings.html       # Configuration page
│   └── launch.html         # Transcription interface
├── app.log                 # System logs (auto-generated)
└── README.md               # This file
```

## 🛠️ Development

---

**Note**: This interface is optimized for 480x320 screens but works on any resolution.