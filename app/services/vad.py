import base64
import time
import asyncio
import threading
import logging
from datetime import datetime
import torch
import numpy as np
from silero_vad import get_speech_timestamps

from ..core.enums import TranscriptionState
from ..audio.io import AudioCapture
from .transcription import TranscriptionAndVoiceService

logger = logging.getLogger(__name__)


class VADTranscriptionController:
    """Voice Activity Detection controller for automatic transcription."""
    
    def __init__(self, audio_capture: AudioCapture, 
                 transcription_service: TranscriptionAndVoiceService, 
                 vad_model, silence_timeout: float = 30.0):
        self.audio_capture = audio_capture
        self.transcription_service = transcription_service
        self.vad_model = vad_model
        self.silence_timeout = silence_timeout
        
        self.state = TranscriptionState.IDLE
        self.state_lock = threading.Lock()
        self.vad_thread = None
        self.silence_timer = None
        self.is_monitoring = False
        self.stop_monitoring_event = threading.Event()
        self.last_speech_time = None
        self.vad_check_interval = 0.5

    def start_monitoring(self):
        """Start VAD monitoring."""
        if self.is_monitoring:
            return
            
        self.is_monitoring = True
        self.stop_monitoring_event.clear()
        self.vad_thread = threading.Thread(target=self._vad_monitoring_loop)
        self.vad_thread.daemon = True
        self.vad_thread.start()
        logger.debug("VAD monitoring started")

    def stop_monitoring(self):
        """Stop VAD monitoring."""
        if not self.is_monitoring:
            return
            
        self.is_monitoring = False
        self.stop_monitoring_event.set()
        
        if self.silence_timer:
            self.silence_timer.cancel()
            
        if self.vad_thread:
            self.vad_thread.join()
            
        logger.debug("VAD monitoring stopped")

    def _vad_monitoring_loop(self):
        """Main VAD monitoring loop."""
        while self.is_monitoring and not self.stop_monitoring_event.is_set():
            try:
                speech_detected = self._detect_speech()
                
                with self.state_lock:
                    if speech_detected:
                        self.last_speech_time = datetime.now()
                        self._handle_speech_detected()
                    else:
                        self._handle_no_speech()
                        
                time.sleep(self.vad_check_interval)
            except Exception as e:
                logger.error(f"Erreur dans la boucle VAD: {e}")
                time.sleep(1)

    def _detect_speech(self) -> bool:
        """Detect speech in recent audio chunks."""
        chunks = self.audio_capture.buffer.get_all_chunks()
        if not chunks:
            return False

        min_chunks_required = int(
            (1.0 * self.audio_capture.SAMPLE_RATE) / self.audio_capture.FRAMES_PER_BUFFER
        )
        
        if len(chunks) < min_chunks_required:
            return False

        try:
            audio_data = []
            for chunk_info in chunks[-min_chunks_required:]:
                chunk_bytes = base64.b64decode(chunk_info['base64'])
                chunk_samples = np.frombuffer(chunk_bytes, dtype=np.int16)
                audio_data.extend(chunk_samples)

            audio_array = np.array(audio_data, dtype=np.float32)
            audio_tensor = torch.FloatTensor(audio_array) / 32768.0

            if len(audio_tensor) == 0:
                return False

            speech_timestamps = get_speech_timestamps(
                audio_tensor,
                self.vad_model,
                return_seconds=True,
                sampling_rate=self.audio_capture.SAMPLE_RATE,
                min_speech_duration_ms=200,
                min_silence_duration_ms=100,
                window_size_samples=1536,
                speech_pad_ms=30
            )

            return bool(speech_timestamps)
        except Exception as e:
            logger.error(f"Erreur lors de la détection VAD: {e}")
            return False

    def _handle_speech_detected(self):
        """Handle speech detection event."""
        if self.state == TranscriptionState.IDLE:
            self.state = TranscriptionState.TRANSCRIBING
            self._start_transcription()
        elif self.state == TranscriptionState.WAITING_SILENCE:
            logger.debug("Speech detected - Cancelling silence timeout")
            self.state = TranscriptionState.TRANSCRIBING
            if self.silence_timer:
                self.silence_timer.cancel()
                self.silence_timer = None

    def _handle_no_speech(self):
        """Handle no speech detection event."""
        if self.state == TranscriptionState.TRANSCRIBING:
            logger.debug(f"Silence detected - Starting {self.silence_timeout}s timeout")
            self.state = TranscriptionState.WAITING_SILENCE
            self._start_silence_timer()

    def _start_transcription(self):
        """Start transcription in a separate thread."""
        def run_transcription():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self.transcription_service.run_transcription())
            finally:
                loop.close()

        transcription_thread = threading.Thread(target=run_transcription)
        transcription_thread.daemon = True
        transcription_thread.start()

    def _start_silence_timer(self):
        """Start silence timeout timer."""
        if self.silence_timer:
            self.silence_timer.cancel()
            
        self.silence_timer = threading.Timer(self.silence_timeout, self._on_silence_timeout)
        self.silence_timer.start()

    def _on_silence_timeout(self):
        """Handle silence timeout."""
        with self.state_lock:
            if self.state == TranscriptionState.WAITING_SILENCE:
                logger.info(f"No Voice Detected Since {self.silence_timeout}s - Standing by")
                self.state = TranscriptionState.IDLE
                self.transcription_service.stop_transcription()

    def get_status(self):
        """Get current VAD status."""
        with self.state_lock:
            return {
                'state': self.state.value,
                'is_monitoring': self.is_monitoring,
                'last_speech_time': self.last_speech_time.isoformat() if self.last_speech_time else None,
                'is_transcribing': self.transcription_service.is_transcribing
            }