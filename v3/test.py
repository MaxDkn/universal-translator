import sounddevice as sd
import numpy as np

# === Configuration ===
samplerate = 44100  # Fréquence d'échantillonnage
blocksize = 1024    # Taille des blocs audio

# === Choisir les périphériques physiques ===
# Remplace si nécessaire par les bons "hw:X,0" que tu veux tester
input1_device = 'hw:0,0'
output1_device = 'hw:0,0'
input2_device = 'hw:1,0'
output2_device = 'hw:1,0'

# === Buffers ===
buffer1 = np.zeros((blocksize, 1), dtype='float32')
buffer2 = np.zeros((blocksize, 1), dtype='float32')

# === Callbacks de lecture micro ===
def callback_input1(indata, frames, time, status):
    global buffer1
    if status:
        print("Input1 warning:", status)
    buffer1 = indata.copy()

def callback_input2(indata, frames, time, status):
    global buffer2
    if status:
        print("Input2 warning:", status)
    buffer2 = indata.copy()

# === Création des flux ===
in1 = sd.InputStream(device=input1_device, channels=1, samplerate=samplerate,
                     blocksize=blocksize, callback=callback_input1)

in2 = sd.InputStream(device=input2_device, channels=1, samplerate=samplerate,
                     blocksize=blocksize, callback=callback_input2)

out1 = sd.OutputStream(device=output1_device, channels=1, samplerate=samplerate,
                       blocksize=blocksize)

out2 = sd.OutputStream(device=output2_device, channels=1, samplerate=samplerate,
                       blocksize=blocksize)

# === Lancer les flux ===
in1.start()
in2.start()
out1.start()
out2.start()

print("🎙️ Écoute des micros et diffusion vers les haut-parleurs physiques")
print("Appuie sur Ctrl+C pour quitter.")

try:
    while True:
        out1.write(buffer2)
        out2.write(buffer1)
except KeyboardInterrupt:
    print("\nArrêt demandé.")
finally:
    # Fermeture propre
    in1.close()
    in2.close()
    out1.close()
    out2.close()
