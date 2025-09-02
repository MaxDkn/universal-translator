import os
import asyncio
import logging
import argparse
from typing import List

from .managers.audio_manager import GladiaAudioManager
from .services.transcription import is_gladia_key_valid
from .utils.logging_utils import setup_logging_with_capture
from .audio.devices import list_audio_devices, get_device_info

logger = setup_logging_with_capture()


async def launch_gladia(gladia_key: str, agent_device, phone_device, 
                       agent_language: str, phone_language: str, 
                       silence_timeout: int = 30, prebuffer_seconds: int = 5.5):
    """Launch the Gladia transcription system with dual audio streams."""
    
    agent_to_phone = GladiaAudioManager(
        gladia_key,
        input_device=agent_device,
        output_device=phone_device,
        input_language=agent_language,
        output_language=phone_language,
        silence_timeout=silence_timeout,
        prebuffer_seconds=prebuffer_seconds
    ).start_audio_capture().start_vad_monitoring()

    phone_to_agent = GladiaAudioManager(
        gladia_key,
        input_device=phone_device,
        output_device=agent_device,
        input_language=phone_language,
        output_language=agent_language,
        silence_timeout=silence_timeout,
        prebuffer_seconds=prebuffer_seconds
    ).start_audio_capture().start_vad_monitoring()

    logger.setLevel(logging.DEBUG)
    logger.info(f"TTS enabled - Output device: {phone_device.name} | Input device: {agent_device.name}")
    logger.info('Starting transcription...')

    try:
        while True:
            await asyncio.sleep(0.1)
    except KeyboardInterrupt:
        logger.info("Program interrupted by user.")
    finally:
        agent_to_phone.cleanup()
        phone_to_agent.cleanup()
        logger.info("Goodbye!")


async def main(allowed_languages: List[str] = ["fr", "en", "es", "de"], 
               silence_timeout: float = 15.0, prebuffer_seconds: float = 5.5):
    """Main entry point of the application."""
    
    parser = argparse.ArgumentParser(
        description="Gladia Real-time Audio Transcription and Translation"
    )
    parser.add_argument(
        "--gladia-key", type=str, 
        help="Gladia API key. If omitted, will try the 'GLADIA_KEY' environment variable."
    )
    parser.add_argument("--agent-device-id", type=int, help="Agent device ID")
    parser.add_argument(
        "--agent-language", choices=allowed_languages, default="fr",
        help="Language spoken by the agent."
    )
    parser.add_argument("--phone-device-id", type=int, help="Phone device ID")
    parser.add_argument(
        "--phone-language", choices=allowed_languages, default="en",
        help="Language spoken by the phone."
    )
    parser.add_argument(
        "--list-devices", action="store_true",
        help="List all available audio devices and exit."
    )
    parser.add_argument(
        "--debug", action="store_true", 
        help="Activate debug logs.", default=False
    )

    args = parser.parse_args()

    logger.setLevel(logging.DEBUG if args.debug else logging.INFO)

    if args.list_devices:
        list_audio_devices()
        return

    if args.agent_device_id is None or args.phone_device_id is None:
        logger.error(f"--agent-device-id ({args.agent_device_id}) and "
                    f"--phone-device-id ({args.phone_device_id}) cannot be empty.")
        return

    args.agent_device = get_device_info(device_name="agent", index=args.agent_device_id)
    args.phone_device = get_device_info(device_name="phone", index=args.phone_device_id)

    gladia_key = args.gladia_key or os.getenv("GLADIA_API_KEY")
    gladia_key = is_gladia_key_valid(gladia_key)

    logger.info(f"===== Gladia Live Transcription + Auto VAD + Pre-buffer ({prebuffer_seconds}s) =====")

    await launch_gladia(
        gladia_key,
        agent_device=args.agent_device,
        phone_device=args.phone_device,
        agent_language=args.agent_language,
        phone_language=args.phone_language,
        silence_timeout=silence_timeout,
        prebuffer_seconds=prebuffer_seconds
    )


def cli():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        exit(0)


if __name__ == "__main__":
    cli()
    