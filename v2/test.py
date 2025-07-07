import os
import json
import asyncio
import logging
import requests
import pyaudio
import base64
import time
from websockets.asyncio.client import connect

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TranscriptionTester:
    def __init__(self, gladia_key: str):
        self.gladia_key = gladia_key
        self.SAMPLE_RATE = 16000
        self.CHANNELS = 1
        self.FORMAT = pyaudio.paInt16
        self.FRAMES_PER_BUFFER = 3200
        
        # Configuration avec traduction français -> anglais
        self.STREAMING_CONFIG = {
            "encoding": "wav/pcm",
            "sample_rate": self.SAMPLE_RATE,
            "bit_depth": 16,
            "channels": self.CHANNELS,
            "language_config": {
                "languages": ["fr"],  # Source: français
                "code_switching": False,
            },
            "realtime_processing": {
                "translation": True,
                "translation_config": {
                    "target_languages": ["en"],
                    "model": "enhanced",  # Changé de "base" à "fast"
                    "match_original_utterances": True,
                    "lipsync": True,
                    "context_adaptation": True,
                    "context": "General conversation",
                    "informal": False
                }
            }
        }
    
    def init_session(self):
        """Initialise une session de transcription live"""
        response = requests.post(
            "https://api.gladia.io/v2/live",
            headers={"X-Gladia-Key": self.gladia_key},
            json=self.STREAMING_CONFIG,
            timeout=10
        )
        
        if not response.ok:
            raise Exception(f"Erreur API: {response.status_code} - {response.text}")
        
        return response.json()
    
    async def test_transcription(self, duration_seconds: int = 10):
        """Test la transcription avec traduction pendant X secondes"""
        print(f"🎤 Démarrage du test de transcription (français -> anglais)")
        print(f"📢 Parlez en français pendant {duration_seconds} secondes...")
        
        # Initialise la session
        session_data = self.init_session()
        websocket_url = session_data["url"]
        
        # Initialise PyAudio
        p = pyaudio.PyAudio()
        stream = p.open(
            format=self.FORMAT,
            channels=self.CHANNELS,
            rate=self.SAMPLE_RATE,
            input=True,
            frames_per_buffer=self.FRAMES_PER_BUFFER
        )
        
        try:
            async with connect(websocket_url) as websocket:
                print("✅ Connexion WebSocket établie")
                
                # Enregistre et envoie l'audio
                start_time = time.time()
                while time.time() - start_time < duration_seconds:
                    # Lit l'audio du micro
                    audio_data = stream.read(self.FRAMES_PER_BUFFER, exception_on_overflow=False)
                    
                    # Encode en base64 et envoie
                    audio_b64 = base64.b64encode(audio_data).decode('utf-8')
                    message = {
                        "type": "audio_chunk",
                        "data": {"chunk": audio_b64}
                    }
                    
                    await websocket.send(json.dumps(message))
                    
                    # Vérifie les messages reçus
                    try:
                        response = await asyncio.wait_for(websocket.recv(), timeout=0.01)
                        content = json.loads(response)
                        
                        if content["type"] == "transcript":
                            data = content["data"]
                            print(f"📋 DEBUG - Données reçues: {json.dumps(data, indent=2)}")
                            
                            if data.get("is_final", False):
                                original_text = data["utterance"]["text"]
                                print(f"🇫🇷 Original (FR): {original_text}")
                                
                                # Debug: affiche toute la structure
                                print(f"🔍 Structure complète: {json.dumps(data, indent=2)}")
                                
                                # Cherche la traduction
                                if "translation" in data:
                                    translation = data["translation"]
                                    print(f"📋 Traductions disponibles: {list(translation.keys())}")
                                    if "en" in translation:
                                        translated_text = translation["en"]["text"]
                                        print(f"🇺🇸 Traduit (EN): {translated_text}")
                                    else:
                                        print("❌ Pas de traduction 'en' trouvée")
                                else:
                                    print("❌ Pas de champ 'translation' dans les données")
                                print("-" * 50)
                                    
                    except asyncio.TimeoutError:
                        pass
                    
                    await asyncio.sleep(0.01)
                
                # Signal de fin d'enregistrement
                await websocket.send(json.dumps({"type": "stop_recording"}))
                print("🛑 Fin de l'enregistrement")
                
                # Attend les derniers résultats
                print("⏳ Traitement des derniers résultats...")
                try:
                    while True:
                        response = await asyncio.wait_for(websocket.recv(), timeout=3.0)
                        content = json.loads(response)
                        
                        if content["type"] == "transcript":
                            data = content["data"]
                            if data.get("is_final", False):
                                original_text = data["utterance"]["text"]
                                print(f"🇫🇷 Final (FR): {original_text}")
                                
                                if "translation" in data and "en" in data["translation"]:
                                    translated_text = data["translation"]["en"]["text"]
                                    print(f"🇺🇸 Final (EN): {translated_text}")
                        
                        elif content["type"] == "post_final_transcript":
                            print("✅ Transcription terminée")
                            break
                            
                except asyncio.TimeoutError:
                    print("⚠️  Timeout atteint, fin du traitement")
        
        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()

def main():
    # Récupère la clé API
    gladia_key = os.getenv("GLADIA_API_KEY")
    if not gladia_key:
        print("❌ Erreur: Variable d'environnement GLADIA_API_KEY non définie")
        return
    
    # Teste la clé
    try:
        response = requests.get(
            "https://api.gladia.io/v2/pre-recorded",
            headers={"x-gladia-key": gladia_key}
        )
        if not response.ok:
            print("❌ Erreur: Clé API Gladia invalide")
            return
        print("✅ Clé API Gladia valide")
    except Exception as e:
        print(f"❌ Erreur de validation de la clé: {e}")
        return
    
    # Lance le test
    tester = TranscriptionTester(gladia_key)
    
    try:
        asyncio.run(tester.test_transcription(duration_seconds=15))
    except KeyboardInterrupt:
        print("\n🛑 Test interrompu par l'utilisateur")
    except Exception as e:
        print(f"❌ Erreur pendant le test: {e}")

if __name__ == "__main__":
    main()