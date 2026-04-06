from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MediaType(str, Enum):
    BOOK = "book"
    MOVIE = "movie"
    TV = "tv"
    MUSIC = "music"
    AUDIOBOOK = "audiobook"


class ItemStatus(str, Enum):
    GRABBED = "grabbed"
    MONITORING = "monitoring"
    COMPLETED = "completed"
    NOT_FOUND = "not_found"
    REVIEW = "review"
    FAILED = "failed"
    SKIPPED = "skipped"


class ProcessMode(str, Enum):
    HANDOFF = "handoff"
    DIRECT = "direct"


class LLMMatch(str, Enum):
    YES = "yes"
    NO = "no"
    UNCERTAIN = "uncertain"


@dataclass
class CsvRow:
    type: MediaType
    title: str
    creator: str
    year: str
    season: str
    notes: str
    source_csv: str = ""

    @property
    def state_key(self) -> str:
        return f"{self.type.value}|{self.title.lower().strip()}|{self.creator.lower().strip()}"


@dataclass
class SearchResult:
    title: str
    indexer: str
    size_bytes: int
    download_url: str
    seeders: int = 0
    protocol: str = ""  # "usenet" or "torrent"
    category: str = ""
    guid: str = ""

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024 * 1024)


@dataclass
class Classification:
    match: LLMMatch
    format_detected: str = ""
    is_omnibus: bool = False
    reason: str = ""


@dataclass
class ProcessResult:
    status: ItemStatus
    mode: ProcessMode
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class StateEntry:
    status: ItemStatus
    mode: ProcessMode
    source_csv: str
    timestamp: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class StatusResult:
    state_key: str
    current_status: ItemStatus
    has_file: bool = False
    download_progress: float | None = None
    message: str = ""
