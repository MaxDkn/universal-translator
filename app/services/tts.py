import io
import logging
import edge_tts
from pydub import AudioSegment
from audio.io import AudioPlayback

logger = logging.getLogger(__name__)


class TTSService:
    """Text-to-Speech service using Edge TTS."""
    
    def __init__(self, playback_audio: AudioPlayback, voice: str = "fr-FR-DeniseNeural"):
        self.playback_audio = playback_audio
        self.playback_buffer = playback_audio.buffer
        self.voice = voice

    async def synthesize_and_queue(self, text: str):
        """Synthesize text and add it to the playback buffer."""
        if not text.strip():
            return

        logger.debug(f"TTS synthesis [{self.voice}]: {text[:50]}...")
        
        try:
            communicate = edge_tts.Communicate(text=text, voice=self.voice)
            audio_data = b""
            
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_data += chunk["data"]

            if not audio_data:
                logger.error("No audio received from Edge TTS")
                return

            converted_audio = self._convert_audio_format(audio_data)
            if converted_audio:
                self._queue_audio_chunks(converted_audio)
            else:
                logger.error("Audio conversion failed")
                
        except Exception as e:
            logger.error(f"Error in TTS synthesis: {e}")

    def _convert_audio_format(self, audio_data: bytes) -> bytes:
        """Convert audio to PyAudio format."""
        try:
            audio_segment = AudioSegment.from_mp3(io.BytesIO(audio_data))
            audio_segment = audio_segment.set_frame_rate(self.playback_audio.SAMPLE_RATE)
            audio_segment = audio_segment.set_channels(1)
            audio_segment = audio_segment.set_sample_width(2)
            return audio_segment.raw_data
        except Exception as e:
            logger.error(f"Audio conversion error: {e}")
            return b''

    def _queue_audio_chunks(self, audio_data: bytes, chunk_size: int = 1024):
        """Split and send audio to the playback buffer."""
        try:
            for i in range(0, len(audio_data), chunk_size):
                chunk = audio_data[i:i + chunk_size]
                if len(chunk) > 0:
                    self.playback_buffer.add_audio_chunk(chunk)
        except Exception as e:
            logger.error(f"Error queueing audio chunks: {e}")
