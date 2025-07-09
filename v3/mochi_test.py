import sounddevice as sd
import numpy as np
import torch
from moshi import HibikiModel

# --- Paramètres audio ---
SAMPLE_RATE = 16000
CHUNK_DURATION = 2  # en secondes
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION)

# --- Charger le modèle Hibiki ---
model = HibikiModel.from_pretrained("kyutai/hibiki-1b-pytorch-bf16")

print("✅ Modèle chargé. Parlez...")

def callback(indata, frames, time, status):
    if status:
        print(f"⚠️ Status audio : {status}")

    audio_np = indata[:, 0]  # Mono
    audio_tensor = torch.from_numpy(audio_np).float().unsqueeze(0)

    with torch.no_grad():
        output_audio = model(audio_tensor, src_lang="fr", tgt_lang="en")

    out_np = output_audio.squeeze().cpu().numpy()
    sd.play(out_np, SAMPLE_RATE)

# --- Boucle de capture audio ---
with sd.InputStream(channels=1, samplerate=SAMPLE_RATE, blocksize=CHUNK_SIZE, callback=callback):
    print("🎤 Écoute en cours... (Ctrl+C pour quitter)")
    try:
        while True:
            sd.sleep(1000)
    except KeyboardInterrupt:
        print("\n👋 Fin du programme.")
