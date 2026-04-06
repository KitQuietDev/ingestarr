from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _get_int(key: str, default: int) -> int:
    v = os.environ.get(key)
    if v is None:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _get_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.lower() in ("true", "1", "yes")


@dataclass
class ServiceConfig:
    url: str
    api_key: str = ""
    username: str = ""
    password: str = ""
    enabled: bool = True


@dataclass
class Config:
    # Prowlarr
    prowlarr: ServiceConfig = field(default_factory=lambda: ServiceConfig(""))
    # Download clients
    sabnzbd: ServiceConfig = field(default_factory=lambda: ServiceConfig(""))
    qbittorrent: ServiceConfig = field(default_factory=lambda: ServiceConfig(""))
    # *Arr apps
    radarr: ServiceConfig = field(default_factory=lambda: ServiceConfig(""))
    sonarr: ServiceConfig = field(default_factory=lambda: ServiceConfig(""))
    lidarr: ServiceConfig = field(default_factory=lambda: ServiceConfig(""))
    readarr: ServiceConfig = field(default_factory=lambda: ServiceConfig(""))
    # LLM (Ollama, OpenRouter, or any OpenAI-compatible endpoint)
    ollama_url: str = ""
    ollama_model: str = "gemma3:27b"
    ollama_api_key: str = "ollama"
    # Behavior
    process_delay: int = 30
    search_delay: int = 5
    fallback_to_direct: bool = False
    lidarr_mode: str = "handoff"
    readarr_enabled: bool = False
    log_level: str = "INFO"
    # Paths
    data_dir: Path = field(default_factory=lambda: Path("/data"))
    books_download_path: str = "/books"
    audiobooks_download_path: str = "/audiobooks"

    @property
    def input_dir(self) -> Path:
        return self.data_dir / "input"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "input" / "processed"

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def review_dir(self) -> Path:
        return self.data_dir / "review"


def load_config(env_file: str | Path | None = None) -> Config:
    if env_file:
        load_dotenv(env_file)
    else:
        load_dotenv()

    def _svc(prefix: str) -> ServiceConfig:
        url = _get(f"{prefix}_URL")
        return ServiceConfig(
            url=url,
            api_key=_get(f"{prefix}_API_KEY"),
            username=_get(f"{prefix}_USERNAME"),
            password=_get(f"{prefix}_PASSWORD"),
            enabled=bool(url),
        )

    return Config(
        prowlarr=_svc("PROWLARR"),
        sabnzbd=_svc("SABNZBD"),
        qbittorrent=_svc("QBITTORRENT"),
        radarr=_svc("RADARR"),
        sonarr=_svc("SONARR"),
        lidarr=_svc("LIDARR"),
        readarr=_svc("READARR"),
        ollama_url=_get("OLLAMA_URL", "http://192.168.1.131:11434"),
        ollama_model=_get("OLLAMA_MODEL", "gemma3:27b"),
        ollama_api_key=_get("OLLAMA_API_KEY", "ollama"),
        process_delay=_get_int("PROCESS_DELAY_SECONDS", 30),
        search_delay=_get_int("SEARCH_DELAY_SECONDS", 5),
        fallback_to_direct=_get_bool("FALLBACK_TO_DIRECT", False),
        lidarr_mode=_get("LIDARR_MODE", "handoff"),
        readarr_enabled=_get_bool("READARR_ENABLED", False),
        log_level=_get("LOG_LEVEL", "INFO"),
        data_dir=Path(_get("DATA_DIR", "/data")),
        books_download_path=_get("BOOKS_DOWNLOAD_PATH", "/books"),
        audiobooks_download_path=_get("AUDIOBOOKS_DOWNLOAD_PATH", "/audiobooks"),
    )
