#!/bin/bash
echo "Installing Gladia Transcription System..."

mkdir -p logs/audio

if command -v apt-get &> /dev/null; then
    echo "Installing system dependencies for Ubuntu/Debian..."
    sudo apt-get update
    sudo apt-get install -y portaudio19-dev python3-dev
fi

if command -v yum &> /dev/null; then
    echo "Installing system dependencies for CentOS/RHEL..."
    sudo yum install -y portaudio-devel python3-devel
fi

pip install -r requirements.txt

echo "Installation completed!"
echo "Usage: python main.py --list-devices to see available audio devices"