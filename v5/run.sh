#!/bin/bash
PID=$(lsof -ti :8000)
if [ -n "$PID" ]; then
    kill -9 $PID
    echo "Processus sur le port 8000 (PID $PID) tué."
else
    echo "Aucun processus ne tourne sur le port 8000."
fi

../v2/.venv/bin/python server.py &

sleep 1.5

if command -v gnome-terminal &> /dev/null; then
    gnome-terminal -- bash -c 'chromium-browser --app=http://0.0.0.0:8000 --start-fullscreen --disable-gpu --disable-software-rasterizer; exec bash'
elif command -v x-terminal-emulator &> /dev/null; then
    x-terminal-emulator -e bash -c 'chromium-browser --app=http://0.0.0.0:8000 --start-fullscreen --disable-gpu --disable-software-rasterizer; exec bash'
elif command -v lxterminal &> /dev/null; then
    lxterminal -e bash -c 'chromium-browser --app=http://0.0.0.0:8000 --start-fullscreen --disable-gpu --disable-software-rasterizer'
else
    echo "Aucun terminal compatible trouvé. Lancement direct de Chromium..."
    chromium-browser --app=http://0.0.0.0:8000 --start-fullscreen --disable-gpu --disable-software-rasterizer
fi
