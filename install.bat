@echo off
echo Installing Gladia Transcription System...

rem Créer les dossiers nécessaires
if not exist "logs\audio" mkdir logs\audio

rem Installation des dépendances Python
pip install -r requirements.txt

echo Installation completed!
echo Usage: python main.py --list-devices to see available audio devices
pause