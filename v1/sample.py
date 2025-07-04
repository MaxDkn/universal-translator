import asyncio
import base64
import json
import sys
from datetime import time
from typing import Literal, TypedDict

import pyaudio
import requests
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosedOK

## Constants
GLADIA_API_URL = "https://api.gladia.io"

# Variable globale pour contrôler l'arrêt
is_stop = False

## Type definitions
class InitiateResponse(TypedDict):
    id: str
    url: str


class LanguageConfiguration(TypedDict):
    languages: list[str] | None
    code_switching: bool | None


class StreamingConfiguration(TypedDict):
    # This is a reduced set of options. For a full list, see the API documentation.
    # https://docs.gladia.io/api-reference/v2/live/init
    encoding: Literal["wav/pcm", "wav/alaw", "wav/ulaw"]
    bit_depth: Literal[8, 16, 24, 32]
    sample_rate: Literal[8_000, 16_000, 32_000, 44_100, 48_000]
    channels: int
    language_config: LanguageConfiguration | None


## Helpers
def get_gladia_key() -> str:
    if len(sys.argv) != 2 or not sys.argv[1]:
        print("You must provide a Gladia key as the first argument.")
        exit(1)
    return sys.argv[1]


def init_live_session(config: StreamingConfiguration) -> InitiateResponse:
    gladia_key = get_gladia_key()
    response = requests.post(
        f"{GLADIA_API_URL}/v2/live",
        headers={"X-Gladia-Key": gladia_key},
        json=config,
        timeout=3,
    )
    if not response.ok:
        print(f"{response.status_code}: {response.text or response.reason}")
        exit(response.status_code)
    return response.json()


async def print_messages_from_socket(socket: ClientConnection) -> None:
    global is_stop
    async for message in socket:
        content = json.loads(message)
        if content["type"] == "transcript" and content["data"]["is_final"]:
            text = content["data"]["utterance"]["text"].strip()
            print(f"text: {text}")
        if content["type"] == "post_final_transcript":
            print("\n################ End of session ################\n")
            print(json.dumps(content, indent=2, ensure_ascii=False))
            is_stop = True  # Arrêter quand la session se termine
            break


async def stop_recording(websocket: ClientConnection) -> None:
    print(">>>>> Ending the recording…")
    await websocket.send(json.dumps({"type": "stop_recording"}))
    await asyncio.sleep(0)


## Sample code
P = pyaudio.PyAudio()

CHANNELS = 1
FORMAT = pyaudio.paInt16
FRAMES_PER_BUFFER = 3200
SAMPLE_RATE = 16_000

STREAMING_CONFIGURATION: StreamingConfiguration = {
    "encoding": "wav/pcm",
    "sample_rate": SAMPLE_RATE,
    "bit_depth": 16,  # It should match the FORMAT value
    "channels": CHANNELS,
    "language_config": {
        "languages": [],
        "code_switching": True,
    },
}


async def send_audio(socket: ClientConnection) -> None:
    global is_stop
    stream = P.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=FRAMES_PER_BUFFER,
    )

    while not is_stop:
        try:
            data = stream.read(FRAMES_PER_BUFFER)
            data = base64.b64encode(data).decode("utf-8")
            json_data = json.dumps({"type": "audio_chunk", "data": {"chunk": str(data)}})
            await socket.send(json_data)
            await asyncio.sleep(0.1)  # Send audio every 100ms
        except ConnectionClosedOK:
            break
    
    stream.stop_stream()
    stream.close()


async def check_stop_condition() -> None:
    """Fonction pour vérifier périodiquement la condition d'arrêt"""
    global is_stop
    while not is_stop:
        await asyncio.sleep(0.1)  # Vérifier toutes les 100ms
        # Ici vous pouvez ajouter d'autres conditions pour définir is_stop = True
        # Par exemple : vérifier un fichier, une base de données, etc.


async def wait_for_stop_input() -> None:
    """Attendre que l'utilisateur tape 'stop' pour arrêter l'enregistrement"""
    global is_stop
    loop = asyncio.get_running_loop()
    
    while not is_stop:
        try:
            # Créer une tâche qui attend l'input utilisateur
            user_input = await loop.run_in_executor(None, input, "Tapez 'stop' pour arrêter l'enregistrement: ")
            if user_input.lower().strip() == 'stop':
                is_stop = True
                break
        except KeyboardInterrupt:
            is_stop = True
            break


async def run_transcription():
    """Fonction principale pour lancer la transcription"""
    global is_stop
    is_stop = False  # Réinitialiser la variable
    
    try:
        response = init_live_session(STREAMING_CONFIGURATION)
        
        async with connect(response["url"]) as websocket:
            print("\n################ Begin session ################\n")
            print("Transcription en cours... Tapez 'stop' pour arrêter")
            
            # Créer toutes les tâches
            send_audio_task = asyncio.create_task(send_audio(websocket))
            print_messages_task = asyncio.create_task(print_messages_from_socket(websocket))
            stop_check_task = asyncio.create_task(check_stop_condition())
            stop_input_task = asyncio.create_task(wait_for_stop_input())
            
            # Attendre que l'une des tâches se termine
            done, pending = await asyncio.wait(
                [send_audio_task, print_messages_task, stop_check_task, stop_input_task],
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # Arrêter l'enregistrement proprement
            if not is_stop:
                is_stop = True
                await stop_recording(websocket)
            
            # Annuler les tâches restantes
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                    
    except Exception as e:
        print(f"Erreur pendant la transcription: {e}")
    finally:
        print("Transcription terminée.\n")


def get_user_choice() -> bool:
    """Demander à l'utilisateur s'il veut lancer la transcription"""
    while True:
        try:
            choice = input("Voulez-vous lancer la transcription ? (oui/non): ").lower().strip()
            if choice in ['oui', 'o', 'yes', 'y']:
                return True
            elif choice in ['non', 'n', 'no']:
                return False
            else:
                print("Réponse non reconnue. Veuillez répondre par 'oui' ou 'non'.")
        except KeyboardInterrupt:
            print("\nAu revoir !")
            return False


async def main():
    """Boucle principale du programme"""
    print("=== Gladia Live Transcription ===\n")
    
    while True:
        if get_user_choice():
            await run_transcription()
        else:
            print("En attente...\n")
            await asyncio.sleep(1)  # Petite pause avant de reposer la question


def set_stop():
    """Fonction utilitaire pour arrêter l'enregistrement depuis l'extérieur"""
    global is_stop
    is_stop = True


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProgramme interrompu par l'utilisateur. Au revoir !")
    except Exception as e:
        print(f"Erreur inattendue: {e}")
    finally:
        # Nettoyer PyAudio
        P.terminate()