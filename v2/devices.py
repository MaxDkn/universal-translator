import pyaudio


def list_audio_devices():
    """List all available real audio input devices (ignore virtual/system drivers)"""
    p = pyaudio.PyAudio()
    devices = []
    seen = set()
    for i in range(p.get_device_count()):
        try:
            device_info = p.get_device_info_by_index(i)
            name = str(device_info['name'])
            if (int(device_info['maxInputChannels']) > 0 
                and name not in seen
                and "mapper" not in name.lower()
                and "wave" not in name.lower()
                and "primary" not in name.lower()):
                seen.add(name)
                devices.append((i, str(name), int(device_info['defaultSampleRate'])))
        except:
            continue

    p.terminate()
    return sorted(devices, key=lambda d: d[1].lower())  # sort by name



if __name__ == "__main__":
    print("=== Physical/usable audio input devices ===")
    devices = list_audio_devices()
    for index, name, sample_rate in devices:
        print(f"[{index:2d}] {name:<60} ({sample_rate:,} Hz)")
