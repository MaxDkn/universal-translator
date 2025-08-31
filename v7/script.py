import sounddevice as sd # type: ignore
import numpy as np
import subprocess
import tempfile
import time
import os

# Paramètres audio
RATE = 16000  # fréquence d'échantillonnage en Hz
CHANNELS = 1  # mono

# Config Moshi
HF_REPO = "kyutai/hibiki-1b-mlx-bf16"
CFG_COEF = 3  # guidance pour conserver la voix, ajustable
CMD_PREFIX = ["python3", "-m", "moshi_mlx.run_inference"]

# Fonction pour enregistrer un segment audio
def record_segment(duration=5):
    print(f"Enregistrement de {duration}s… Parlez maintenant.")
    recording = sd.rec(int(duration * RATE), samplerate=RATE, channels=CHANNELS, dtype='int16')
    sd.wait()
    return recording.tobytes()

def save_wav(raw_bytes, path):
    import wave
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # int16
        wf.setframerate(RATE)
        wf.writeframes(raw_bytes)

def main_loop(segment_duration=5):
    print("Démarrage du mode de traduction Hibiki en temps réel.")
    while True:
        raw = record_segment(segment_duration)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as src:
            save_wav(raw, src.name)
        out = src.name.replace(".wav", "_out_en.wav")
        cmd = CMD_PREFIX + [src.name, out, "--hf-repo", HF_REPO, "--cfg-coef", str(CFG_COEF)]
        print("Appel inference:", " ".join(cmd))
        subprocess.run(cmd)
        print(f"Fichier de traduction généré : {out}")
        os.remove(src.name)

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print("Arrêt du script.")
