# 🎧 Raspberry Pi Universal Translator

**Raspberry Pi Universal Translator** is a real-time, bidirectional voice translation device designed for seamless communication between people speaking different languages — all without a screen or keyboard.

The system connects:
- On one side, to a **headset** (microphone + headphones) worn by the user
- On the other, to a **3.5mm jack audio interface** connected to an external device or speaker

🧠 The core concept: it acts as a smart interpreter.  
When the user speaks, their voice is transcribed, translated, and then synthesized in the target language — which is played through the jack output.  
Likewise, when someone responds in their language via the jack input, the process is reversed and the translation is played in the user's headset.

## 🔄 Audio Streams

The project handles **4 simultaneous audio streams**:
- 🎙 Two inputs:
  - From the user's microphone
  - From the external device's microphone (via jack)
- 🔊 Two outputs:
  - To the user's headphones (translated voice of the external person)
  - To the jack output (translated voice of the user)

## 💡 Key Features (Planned)

- Real-time, low-latency speech translation
- Offline or online transcription and translation
- Multi-language support
- Hands-free, headless operation
- Lightweight and portable — runs on a Raspberry Pi

## 🚧 Status

> This project is currently in active development. Code, hardware diagrams, and setup instructions will be added progressively.

Stay tuned!

To try tomorrow:
```python
import sounddevice as sd
import numpy as np
import threading
import queue
import time

RATE = 16000
CHUNK = 1024

# Buffers d'entrée (les micros remplissent ça)
buffer_mic_user = queue.Queue()
buffer_mic_env = queue.Queue()

# Buffers de sortie (à jouer via les haut-parleurs)
buffer_out_user = queue.Queue()
buffer_out_env = queue.Queue()

# Remplir les buffers de sortie avec du son bidon pour tester (ex: un sinus)
duration = 5  # secondes
t = np.linspace(0, duration, int(RATE * duration), endpoint=False)
fake_sound1 = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
fake_sound2 = (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

# Fractionner en blocs CHUNK
for i in range(0, len(fake_sound1), CHUNK):
    buffer_out_user.put(fake_sound1[i:i+CHUNK])
    buffer_out_env.put(fake_sound2[i:i+CHUNK])

# Recorder: lit le micro en continu
def record_stream(device_name, out_buffer):
    def callback(indata, frames, time, status):
        if status:
            print(f"[{device_name} STATUS]", status)
        out_buffer.put(indata.copy())
    stream = sd.InputStream(
        device=device_name,
        samplerate=RATE,
        blocksize=CHUNK,
        channels=1,
        dtype='float32',
        callback=callback,
    )
    stream.start()

# Player: joue le contenu d’un buffer
def play_stream(device_name, in_buffer):
    def callback(outdata, frames, time, status):
        if status:
            print(f"[{device_name} STATUS]", status)
        if not in_buffer.empty():
            data = in_buffer.get()
            outdata[:] = data.reshape(-1, 1)
        else:
            outdata[:] = np.zeros((CHUNK, 1), dtype='float32')
    stream = sd.OutputStream(
        device=device_name,
        samplerate=RATE,
        blocksize=CHUNK,
        channels=1,
        dtype='float32',
        callback=callback,
    )
    stream.start()

# Démarrer les 4 flux
record_stream('dmic_user', buffer_mic_user)
record_stream('dmic_env', buffer_mic_env)
play_stream('dout_user', buffer_out_user)
play_stream('dout_env', buffer_out_env)

# Garder le script vivant
print("Enregistrement & lecture actifs. Ctrl+C pour quitter.")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Arrêt.")
```
