import logging
from silero_vad import load_silero_vad

from ..audio.devices import AudioDevice
from ..audio.buffers import SharedAudioBuffer, SharedAudioPlaybackBuffer
from ..audio.io import AudioCapture, AudioPlayback
from ..services.tts import TTSService
from ..services.transcription import TranscriptionAndVoiceService
from ..services.vad import VADTranscriptionController

logger = logging.getLogger(__name__)


class GladiaAudioManager:
    """Main manager that orchestrates all audio processing components."""
    
    voice_edge_tts = {
        "fr": "fr-FR-DeniseNeural",
        "en": "en-US-AriaNeural",
        "es": "es-ES-AlvaroNeural",
        "de": "de-DE-ConradNeural"
    }

    def __init__(self, gladia_key: str, input_device: AudioDevice, 
                 output_device: AudioDevice, input_language: str = "fr", 
                 output_language: str = "en", silence_timeout: float = 30.0, 
                 prebuffer_seconds: float = 5.5):
        
        self.audio_buffer = SharedAudioBuffer(prebuffer_seconds=prebuffer_seconds)
        self.audio_capture = AudioCapture(self.audio_buffer, input_device)
        
        self.playback_buffer = SharedAudioPlaybackBuffer()
        self.audio_playback = AudioPlayback(self.playback_buffer, output_device)
        
        voice = self.voice_edge_tts.get(output_language, "en-US-AriaNeural")
        self.tts_service = TTSService(self.audio_playback, voice=voice)
        logger.debug(f"TTS enabled with voice: {voice}")
        
        self.transcription_service = TranscriptionAndVoiceService(
            gladia_key=gladia_key,
            audio_capture=self.audio_capture,
            tts_service=self.tts_service,
            agent_language=input_language,
            target_language=output_language,
        )
        
        self.vad_controller = VADTranscriptionController(
            audio_capture=self.audio_capture,
            transcription_service=self.transcription_service,
            vad_model=load_silero_vad(),
            silence_timeout=silence_timeout
        )

    def start_audio_capture(self):
        """Start audio capture and playback."""
        logger.debug(f"Starting audio capture with {self.audio_buffer.prebuffer_seconds}s pre-buffer...")
        self.audio_capture.start_capture()
        if self.audio_playback:
            self.audio_playback.start_playback()
        return self

    def stop_audio_capture(self):
        """Stop audio capture and playback."""
        logger.debug("Stopping audio capture...")
        self.audio_capture.stop_capture()
        if self.audio_playback:
            self.audio_playback.stop_playback()

    def start_vad_monitoring(self):
        """Start VAD monitoring."""
        self.vad_controller.start_monitoring()
        return self

    def stop_vad_monitoring(self):
        """Stop VAD monitoring."""
        self.vad_controller.stop_monitoring()

    def get_buffer_stats(self):
        """Get buffer statistics."""
        stats = {
            'total_chunks': len(self.audio_buffer.get_all_chunks()),
            'prebuffer_chunks': len(self.audio_buffer.get_prebuffer_chunks()),
            'prebuffer_seconds': self.audio_buffer.prebuffer_seconds,
            'is_recording': self.audio_buffer.is_recording,
            'listeners': len(self.audio_buffer.listeners)
        }
        
        if self.playback_buffer:
            stats['playback_buffer_size'] = len(self.playback_buffer.buffer)
            
        return stats

    def get_vad_status(self):
        """Get VAD status."""
        return self.vad_controller.get_status()

    def cleanup(self):
        """Cleanup all resources."""
        self.vad_controller.stop_monitoring()
        self.audio_capture.cleanup()
        if self.audio_playback:
            self.audio_playback.cleanup()
