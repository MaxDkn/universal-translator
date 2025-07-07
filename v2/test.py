import io
import asyncio
import logging
import pyaudio
import edge_tts
from pydub import AudioSegment
import threading
import queue
import time
import os

logger = logging.getLogger(__name__)

class DirectAudioPlayer:
    def __init__(self):
        self.p = pyaudio.PyAudio()
        self.stream = None
        self.audio_queue = queue.Queue()
        self.is_playing = False
        self.play_thread = None
        
        # Configuration audio plus compatible
        self.SAMPLE_RATE = 22050  # Plus compatible que 44100
        self.CHANNELS = 1         # Mono plus stable
        self.FORMAT = pyaudio.paInt16

    def list_audio_devices(self):
        """Liste les périphériques audio disponibles"""
        print("\n=== Périphériques Audio Disponibles ===")
        for i in range(self.p.get_device_count()):
            try:
                info = self.p.get_device_info_by_index(i)
                if info['maxOutputChannels'] > 0:
                    print(f"[{i}] {info['name']} - {info['maxOutputChannels']} canaux - {info['defaultSampleRate']} Hz")
            except:
                continue
        
    def find_best_output_device(self):
        """Trouve le meilleur périphérique de sortie"""
        try:
            # Essayer le périphérique par défaut
            default_info = self.p.get_default_output_device_info()
            return default_info['index']
        except:
            # Chercher un périphérique qui fonctionne
            for i in range(self.p.get_device_count()):
                try:
                    info = self.p.get_device_info_by_index(i)
                    if info['maxOutputChannels'] > 0:
                        # Tester si on peut ouvrir ce périphérique
                        test_stream = self.p.open(
                            format=self.FORMAT,
                            channels=1,
                            rate=22050,
                            output=True,
                            output_device_index=i,
                            frames_per_buffer=512
                        )
                        test_stream.close()
                        return i
                except:
                    continue
        return None
        
    def start_playback(self):
        """Démarre la lecture audio avec détection automatique"""
        if self.is_playing:
            return
            
        self.list_audio_devices()
        device_index = self.find_best_output_device()
        
        if device_index is None:
            logger.error("Aucun périphérique audio trouvé")
            return False
            
        try:
            config = {
                'format': self.FORMAT,
                'channels': self.CHANNELS,
                'rate': self.SAMPLE_RATE,
                'output': True,
                'frames_per_buffer': 512,
                'output_device_index': device_index
            }
            
            self.stream = self.p.open(**config)
            
            self.is_playing = True
            self.play_thread = threading.Thread(target=self._playback_loop)
            self.play_thread.daemon = True
            self.play_thread.start()
            
            device_name = self.p.get_device_info_by_index(device_index)['name']
            logger.info(f"✓ Lecture audio démarrée sur: {device_name}")
            return True
            
        except Exception as e:
            logger.error(f"Erreur ouverture stream audio: {e}")
            return False
            
    def stop_playback(self):
        """Arrête la lecture audio"""
        self.is_playing = False
        
        if self.play_thread:
            self.play_thread.join(timeout=2)
            
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except:
                pass
            
        logger.info("Lecture audio arrêtée")
        
    def add_audio_chunk(self, audio_data: bytes):
        """Ajoute un chunk audio à la queue de lecture"""
        try:
            self.audio_queue.put(audio_data, timeout=1)
        except queue.Full:
            logger.warning("Queue audio pleine, chunk ignoré")
            
    def _playback_loop(self):
        """Boucle de lecture audio"""
        while self.is_playing:
            try:
                audio_data = self.audio_queue.get(timeout=0.1)
                if self.stream and len(audio_data) > 0:
                    self.stream.write(audio_data)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Erreur lecture audio: {e}")
                break
                
    def cleanup(self):
        self.stop_playback()
        self.p.terminate()

class TTSService:
    def __init__(self, audio_player: DirectAudioPlayer, voice: str = "en-US-AriaNeural"):
        self.audio_player = audio_player
        self.voice = voice
        
    async def synthesize_and_play(self, text: str):
        """Synthétise le texte et le joue directement"""
        if not text.strip():
            return
            
        logger.info(f"Synthèse [{self.voice}]: {text}")
        
        try:
            communicate = edge_tts.Communicate(text=text, voice=self.voice)
            
            # Récupérer tout l'audio d'abord
            audio_data = b""
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_data += chunk["data"]
            
            if not audio_data:
                logger.error("Aucun audio reçu d'Edge TTS")
                return
                
            # Convertir et jouer
            converted_audio = self._convert_audio_format(audio_data)
            if converted_audio:
                self._queue_audio_chunks(converted_audio)
                logger.info("Synthèse terminée")
            else:
                logger.error("Échec de conversion audio")
                
        except Exception as e:
            logger.error(f"Erreur synthèse TTS: {e}")
    
    def _convert_audio_format(self, audio_data: bytes) -> bytes:
        """Convertit l'audio au format PyAudio"""
        try:
            # Edge TTS produit du MP3
            audio_segment = AudioSegment.from_mp3(io.BytesIO(audio_data))
            
            # Convertir au format de notre player (mono, 22050 Hz)
            audio_segment = audio_segment.set_frame_rate(self.audio_player.SAMPLE_RATE)
            audio_segment = audio_segment.set_channels(self.audio_player.CHANNELS)
            audio_segment = audio_segment.set_sample_width(2)  # 16-bit
            
            return audio_segment.raw_data
            
        except Exception as e:
            logger.error(f"Erreur conversion audio: {e}")
            return b''
    
    def _queue_audio_chunks(self, audio_data: bytes, chunk_size: int = 1024):
        """Découpe et envoie l'audio au player"""
        try:
            for i in range(0, len(audio_data), chunk_size):
                chunk = audio_data[i:i + chunk_size]
                if len(chunk) > 0:
                    self.audio_player.add_audio_chunk(chunk)
        except Exception as e:
            logger.error(f"Erreur découpe audio: {e}")


async def interactive_mode():
    """Mode interactif avec voix fonctionnelle"""
    print("\n=== Mode Interactif (Anglais) ===")
    print("Tapez du texte en anglais (q pour quitter)")
    
    audio_player = DirectAudioPlayer()
    if not audio_player.start_playback():
        print("❌ Impossible de démarrer l'audio")
        return
        
    tts_service = TTSService(audio_player, voice="en-US-JennyNeural")
    
    try:
        while True:
            text = input("\n> ")
            
            if text.lower() in ['q', 'quit', 'exit']:
                break
                
            if text.strip():
                await tts_service.synthesize_and_play(text)
                
    except KeyboardInterrupt:
        print("\nMode interactif arrêté")
    finally:
        audio_player.cleanup()

if __name__ == "__main__":
    async def main():
        try:
            await interactive_mode()            
        except Exception as e:
            print(f"Erreur: {e}")
            
    asyncio.run(main())