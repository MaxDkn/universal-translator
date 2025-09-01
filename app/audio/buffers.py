import queue
import base64
import threading
from collections import deque
from datetime import datetime, timedelta


class SharedAudioBuffer:
    """Shared buffer for audio data with prebuffering capabilities."""
    
    def __init__(self, max_chunks: int = 1000, prebuffer_seconds: float = 3.0):
        self.buffer = deque(maxlen=max_chunks)
        self.lock = threading.Lock()
        self.is_recording = False
        self.listeners = []
        self.prebuffer_seconds = prebuffer_seconds
        self.prebuffer_duration = timedelta(seconds=prebuffer_seconds)

    def start_recording(self):
        with self.lock:
            self.is_recording = True

    def stop_recording(self):
        with self.lock:
            self.is_recording = False

    def add_chunk(self, chunk_data: bytes):
        with self.lock:
            timestamp = datetime.now()
            chunk_info = {
                'data': chunk_data,
                'timestamp': timestamp,
                'base64': base64.b64encode(chunk_data).decode('utf-8')
            }
            self.buffer.append(chunk_info)
            
            if self.is_recording:
                for listener in self.listeners:
                    try:
                        listener.put_nowait(chunk_info)
                    except queue.Full:
                        pass

    def get_all_chunks(self) -> list:
        with self.lock:
            return list(self.buffer)

    def get_prebuffer_chunks(self) -> list:
        """Retrieve pre-buffer chunks (last X seconds)."""
        with self.lock:
            if not self.buffer:
                return []

            current_time = datetime.now()
            cutoff_time = current_time - self.prebuffer_duration
            prebuffer_chunks = []

            for chunk_info in reversed(self.buffer):
                if chunk_info['timestamp'] >= cutoff_time:
                    prebuffer_chunks.append(chunk_info)
                else:
                    break

            return list(reversed(prebuffer_chunks))

    def register_listener(self) -> queue.Queue:
        listener_queue = queue.Queue(maxsize=100)
        self.listeners.append(listener_queue)
        return listener_queue

    def unregister_listener(self, listener_queue: queue.Queue):
        if listener_queue in self.listeners:
            self.listeners.remove(listener_queue)


class SharedAudioPlaybackBuffer:
    """Shared buffer for audio playback data."""
    
    def __init__(self, max_chunks: int = 1000):
        self.buffer = deque(maxlen=max_chunks)
        self.lock = threading.Lock()
        self.is_playing = False

    def add_audio_chunk(self, chunk_data: bytes):
        with self.lock:
            self.buffer.append(chunk_data)

    def get_chunks_to_play(self) -> list:
        with self.lock:
            if not self.buffer:
                return []
            chunks = list(self.buffer)
            self.buffer.clear()
            return chunks

    def clear(self):
        with self.lock:
            self.buffer.clear()
