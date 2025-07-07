Pour l'instant, la transcription fonctionne correctement, et j'ai bien un pré buffer pour ne rien perdre en audio. La transcription s'affiche correctement dans le terminal, et s'active avec la VAD.

Schéma pour comprendre le projet du traducteur universel:
```
      ┌──────────────────────────────────────────┐
      │        phone-device (auto-language ou en)│
      └──────────────────────────────────────────┘
                     ↑           ↓
           [synthèse vocale] [transcription]
                     ↑           ↓
      ┌──────────────────────────────────────────┐
      │          Raspberry Pi 5 (hub)            │
      └──────────────────────────────────────────┘
                      ↑           ↓
             [transcription]   [synthèse vocale]
                      ↑           ↓
      ┌──────────────────────────────────────────┐
      │    Casque (agent-device, langue: fr)     │
      └──────────────────────────────────────────┘
```

To run:
`uv venv`
`uv pip sync`
`sudo .venv/bin/python script.py --agent-device CM477-30757 --agent-language fr --phone-device CM477-30757 --phone-language auto --gladia-key $GLADIA_API_KEY`