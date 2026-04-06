from __future__ import annotations

import logging

from ..config import Config
from .arr import LidarrClient, RadarrClient, ReadarrClient, SonarrClient
from .ollama import OllamaClient
from .prowlarr import ProwlarrClient
from .qbittorrent import QBittorrentClient
from .sabnzbd import SabnzbdClient

log = logging.getLogger(__name__)


class Services:
    """Container for all external service clients. Initializes only what's configured."""

    def __init__(self, config: Config):
        self.config = config

        # Required
        self.prowlarr = ProwlarrClient(
            config.prowlarr.url, config.prowlarr.api_key
        )
        self.ollama = OllamaClient(
            config.ollama_url, config.ollama_model, config.ollama_api_key
        )

        # Download clients
        self.sabnzbd: SabnzbdClient | None = None
        if config.sabnzbd.enabled:
            self.sabnzbd = SabnzbdClient(
                config.sabnzbd.url, config.sabnzbd.api_key
            )

        self.qbittorrent: QBittorrentClient | None = None
        if config.qbittorrent.enabled:
            self.qbittorrent = QBittorrentClient(
                config.qbittorrent.url,
                config.qbittorrent.username,
                config.qbittorrent.password,
            )

        # *Arr apps
        self.radarr: RadarrClient | None = None
        if config.radarr.enabled:
            self.radarr = RadarrClient(config.radarr.url, config.radarr.api_key)

        self.sonarr: SonarrClient | None = None
        if config.sonarr.enabled:
            self.sonarr = SonarrClient(config.sonarr.url, config.sonarr.api_key)

        self.lidarr: LidarrClient | None = None
        if config.lidarr.enabled:
            self.lidarr = LidarrClient(config.lidarr.url, config.lidarr.api_key)

        self.readarr: ReadarrClient | None = None
        if config.readarr.enabled and config.readarr_enabled:
            self.readarr = ReadarrClient(config.readarr.url, config.readarr.api_key)

    def validate_startup(self) -> list[str]:
        """Check only truly required services at startup. Returns list of fatal errors.

        Only Prowlarr, Ollama, and at least one download client are required to
        start. *Arr apps are validated lazily when the first row needing them is
        processed — a books-only CSV should not fail because Radarr isn't configured.
        """
        errors: list[str] = []

        if not self.config.prowlarr.url:
            errors.append("PROWLARR_URL is required")
        if not self.config.prowlarr.api_key:
            errors.append("PROWLARR_API_KEY is required")
        if not self.config.ollama_url:
            errors.append("OLLAMA_URL is required")

        if not self.sabnzbd and not self.qbittorrent:
            errors.append(
                "At least one download client required (SABNZBD_URL or QBITTORRENT_URL)"
            )

        return errors

    def require_arr(self, service_name: str) -> None:
        """Validate that a specific *arr service is configured. Called lazily by
        media type handlers on first use. Raises ServiceError if not configured."""
        client = getattr(self, service_name, None)
        if client is None:
            from .base import ServiceError
            raise ServiceError(
                service_name,
                f"{service_name} is not configured. Set {service_name.upper()}_URL "
                f"and {service_name.upper()}_API_KEY in .env to use this media type.",
            )

    def summary(self) -> str:
        """Human-readable summary of configured services."""
        lines = ["Configured services:"]
        lines.append(f"  Prowlarr:     {self.config.prowlarr.url}")
        lines.append(f"  Ollama:       {self.config.ollama_url} ({self.config.ollama_model})")
        lines.append(f"  SABnzbd:      {self.config.sabnzbd.url or 'not configured'}")
        lines.append(f"  qBittorrent:  {self.config.qbittorrent.url or 'not configured'}")
        lines.append(f"  Radarr:       {self.config.radarr.url or 'not configured'}")
        lines.append(f"  Sonarr:       {self.config.sonarr.url or 'not configured'}")
        lines.append(f"  Lidarr:       {self.config.lidarr.url or 'not configured'} (mode={self.config.lidarr_mode})")
        lines.append(f"  Readarr:      {self.config.readarr.url or 'not configured'} (enabled={self.config.readarr_enabled})")
        return "\n".join(lines)
