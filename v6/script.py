import sounddevice as sd # type: ignore
from dataclasses import dataclass

card_A_input = 0  
card_A_output = 0
card_B_input = 1  
card_B_output = 1

samplerate = 44100  
blocksize = 1024
channels = 1

def callback_AtoB(indata, outdata, _, _time, status):
    if status:
        print("Status A→B:", status)
    outdata[:] = indata

def callback_BtoA(indata, outdata, _, _time, status):
    if status:
        print("Status B→A:", status)
    outdata[:] = indata

stream_AtoB = sd.Stream(
    samplerate=samplerate,
    blocksize=blocksize,
    dtype='int16',
    channels=channels,
    device=(card_A_input, card_B_output),
    callback=callback_AtoB
)

stream_BtoA = sd.Stream(
    samplerate=samplerate,
    blocksize=blocksize,
    dtype='int16',
    channels=channels,
    device=(card_B_input, card_A_output),
    callback=callback_BtoA
)


@dataclass
class ConfigDevice:
    pass

class GladiaTranslator():
    pass

audio_translator = GladiaTranslator(stt=..., 
                                    vad=..., 
                                    tts=..., 
                                    trad=None,
                                    config_device=ConfigDevice)


print("=== Cross Audio Bridge entre Carte A et Carte B ===")
with stream_AtoB, stream_BtoA:
    try:
        sd.sleep(1000000)
    except KeyboardInterrupt:
        print()
        exit()
