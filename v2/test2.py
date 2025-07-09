import pyaudio
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def debug_all_audio_devices():
    """
    Analyse TOUS les devices audio sans filtre pour débugger les problèmes d'accès
    """
    p = pyaudio.PyAudio()
    
    print("=== ANALYSE COMPLÈTE DES DEVICES AUDIO ===\n")
    
    try:
        device_count = p.get_device_count()
        print(f"Nombre total de devices détectés: {device_count}\n")
        
        for i in range(device_count):
            try:
                info = p.get_device_info_by_index(i)
                
                print(f"--- Device {i} ---")
                print(f"Nom: {info['name']}")
                print(f"Canaux INPUT: {info['maxInputChannels']}")
                print(f"Canaux OUTPUT: {info['maxOutputChannels']}")
                print(f"Sample Rate par défaut: {info['defaultSampleRate']}")
                print(f"Host API: {p.get_host_api_info_by_index(info['hostApi'])['name']}")
                
                # Test d'accès INPUT
                if info['maxInputChannels'] > 0:
                    try:
                        # Test si on peut ouvrir en INPUT
                        test_stream = p.open(
                            format=pyaudio.paInt16,
                            channels=1,
                            rate=int(info['defaultSampleRate']),
                            input=True,
                            input_device_index=i,
                            frames_per_buffer=1024
                        )
                        test_stream.close()
                        print("✅ INPUT: Accessible")
                    except Exception as e:
                        print(f"❌ INPUT: ERREUR - {e}")
                
                # Test d'accès OUTPUT
                if info['maxOutputChannels'] > 0:
                    try:
                        # Test si on peut ouvrir en OUTPUT
                        test_stream = p.open(
                            format=pyaudio.paInt16,
                            channels=1,
                            rate=int(info['defaultSampleRate']),
                            output=True,
                            output_device_index=i,
                            frames_per_buffer=1024
                        )
                        test_stream.close()
                        print("✅ OUTPUT: Accessible")
                    except Exception as e:
                        print(f"❌ OUTPUT: ERREUR - {e}")
                
                print("-" * 40)
                
            except Exception as e:
                print(f"Erreur lors de l'analyse du device {i}: {e}")
                
    except Exception as e:
        logger.error(f"Erreur générale: {e}")
    
    finally:
        p.terminate()

def find_working_input_devices():
    """
    Trouve spécifiquement les devices INPUT qui fonctionnent vraiment
    """
    p = pyaudio.PyAudio()
    working_inputs = []
    
    print("\n=== DEVICES INPUT FONCTIONNELS ===\n")
    
    try:
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            
            if info['maxInputChannels'] > 0:
                try:
                    # Test plusieurs formats et sample rates
                    for sample_rate in [44100, 48000, int(info['defaultSampleRate'])]:
                        for channels in [1, min(2, info['maxInputChannels'])]:
                            try:
                                stream = p.open(
                                    format=pyaudio.paInt16,
                                    channels=channels,
                                    rate=sample_rate,
                                    input=True,
                                    input_device_index=i,
                                    frames_per_buffer=1024
                                )
                                stream.close()
                                
                                device_info = {
                                    'index': i,
                                    'name': info['name'],
                                    'channels': channels,
                                    'sample_rate': sample_rate,
                                    'max_channels': info['maxInputChannels']
                                }
                                working_inputs.append(device_info)
                                print(f"✅ Device {i}: {info['name']} - {channels}ch @ {sample_rate}Hz")
                                break
                            except:
                                continue
                        else:
                            continue
                        break
                except Exception as e:
                    print(f"❌ Device {i}: {info['name']} - Non accessible")
    
    finally:
        p.terminate()
    
    return working_inputs

def test_virtual_device_access():
    """
    Test spécifique des devices virtuels pour l'accès simultané
    """
    p = pyaudio.PyAudio()
    
    print("\n=== TEST DEVICES VIRTUELS ===\n")
    
    virtual_keywords = ['dmix', 'pulse', 'default', 'hw:', 'plughw:']
    
    try:
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            device_name = info['name'].lower()
            
            if any(keyword in device_name for keyword in virtual_keywords):
                print(f"Testing virtual device: {info['name']}")
                
                # Test accès simultané INPUT + OUTPUT
                if info['maxInputChannels'] > 0 and info['maxOutputChannels'] > 0:
                    try:
                        input_stream = p.open(
                            format=pyaudio.paInt16,
                            channels=1,
                            rate=44100,
                            input=True,
                            input_device_index=i,
                            frames_per_buffer=1024
                        )
                        
                        output_stream = p.open(
                            format=pyaudio.paInt16,
                            channels=1,
                            rate=44100,
                            output=True,
                            output_device_index=i,
                            frames_per_buffer=1024
                        )
                        
                        print(f"✅ {info['name']}: INPUT + OUTPUT simultané possible")
                        
                        input_stream.close()
                        output_stream.close()
                        
                    except Exception as e:
                        print(f"❌ {info['name']}: Erreur simultané - {e}")
    
    finally:
        p.terminate()

if __name__ == "__main__":
    # Analyse complète
    debug_all_audio_devices()
    
    # Trouve les inputs fonctionnels
    working_inputs = find_working_input_devices()
    
    # Test devices virtuels
    test_virtual_device_access()
    
    print(f"\n=== RÉSUMÉ ===")
    print(f"Devices INPUT fonctionnels trouvés: {len(working_inputs)}")
    for device in working_inputs:
        print(f"  - {device['name']} (index {device['index']})")