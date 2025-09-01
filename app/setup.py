from setuptools import setup, find_packages

setup(
    name="gladia-transcription",
    version="1.0.0",
    description="Real-time audio transcription and translation system using Gladia API",
    author="Max Deckmyn",
    packages=find_packages(),
    install_requires=[
        "torch>=1.9.0",
        "pyaudio>=0.2.11",
        "requests>=2.25.1",
        "edge-tts>=6.1.0",
        "numpy>=1.21.0",
        "fastapi>=0.68.0",
        "pydub>=0.25.1",
        "jinja2>=3.0.0",
        "websockets>=10.0",
        "silero-vad>=4.0.0",
    ],
    python_requires=">=3.8",
    entry_points={
        "console_scripts": [
            "gladia-transcription=main:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
)