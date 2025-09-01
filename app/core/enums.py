from enum import Enum
from typing import Literal, TypedDict


class TranscriptionState(Enum):
    IDLE = "idle"
    TRANSCRIBING = "transcribing"
    WAITING_SILENCE = "waiting_silence"


class InitiateResponse(TypedDict):
    id: str
    url: str


class LanguageConfiguration(TypedDict):
    languages: list[str] | None
    code_switching: bool | None


class TranslationConfiguration(TypedDict):
    target_languages: list[str]
    context_adaptation: bool
    context: str


class RealtimeProcessingConfiguration(TypedDict):
    translation: bool
    translation_config: TranslationConfiguration | None


class StreamingConfiguration(TypedDict):
    encoding: Literal["wav/pcm", "wav/alaw", "wav/ulaw"]
    bit_depth: Literal[8, 16, 24, 32]
    sample_rate: Literal[8_000, 16_000, 32_000, 44_100, 48_000]
    channels: int
    language_config: LanguageConfiguration | None
    realtime_processing: RealtimeProcessingConfiguration | None
    