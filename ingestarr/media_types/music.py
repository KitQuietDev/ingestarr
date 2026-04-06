from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..models import (
    CsvRow, ItemStatus, ProcessMode, ProcessResult,
    StateEntry, StatusResult,
)
from ..services.base import ServiceError
from .base import HandoffHandler

if TYPE_CHECKING:
    from ..services import Services
    from ..services.arr import LidarrClient

log = logging.getLogger(__name__)


class MusicHandler(HandoffHandler):
    """Music handler — handoff to Lidarr by default, configurable to direct mode."""

    media_type = "music"
    arr_service_name = "lidarr"

    identity_prompt_template = (
        "I'm looking for a music album or artist:\n"
        "Title: {title}\n"
        "Artist: {creator}\n"
        "Year: {year}\n"
        "Notes: {notes}\n\n"
        "What is the exact canonical album title and artist? "
        "If this is an artist name only (not a specific album), say so. "
        "If multiple albums share this name, disambiguate using artist and year."
    )

    def process(self, row: CsvRow, services: Services) -> ProcessResult:
        services.require_arr("lidarr")
        lidarr = services.lidarr
        assert lidarr is not None

        search_term = self._resolve_identity(row, services)
        log.info("Music identity resolved: '%s' → '%s'", row.title, search_term)

        # Try album lookup first, then artist
        try:
            album_results = lidarr.lookup_album(
                f"{row.title} {row.creator}".strip()
            )
        except ServiceError as exc:
            log.error("Lidarr album lookup failed: %s", exc)
            return ProcessResult(
                status=ItemStatus.FAILED,
                mode=ProcessMode.HANDOFF,
                details={"error": str(exc)},
            )

        if not album_results:
            # Try artist-only search
            try:
                artist_results = lidarr.lookup(row.creator or row.title)
            except ServiceError as exc:
                log.error("Lidarr artist lookup failed: %s", exc)
                return ProcessResult(
                    status=ItemStatus.FAILED,
                    mode=ProcessMode.HANDOFF,
                    details={"error": str(exc)},
                )

            if not artist_results:
                log.info("No Lidarr results for '%s'", search_term)
                return ProcessResult(
                    status=ItemStatus.NOT_FOUND,
                    mode=ProcessMode.HANDOFF,
                    details={"search_term": search_term},
                )

            # Add artist
            best_artist = artist_results[0]
            return self._add_artist(best_artist, row, lidarr)

        # Pick best album match
        best = album_results[0]
        if row.year:
            for r in album_results:
                release_date = r.get("releaseDate", "")
                if release_date and release_date.startswith(row.year):
                    best = r
                    break

        foreign_id = best.get("foreignAlbumId", "")

        # We add the artist (Lidarr requires artist to exist for album monitoring)
        artist_info = best.get("artist", {})
        if not artist_info:
            log.warning("Album result missing artist info: %s", best.get("title", "?"))
            return ProcessResult(
                status=ItemStatus.FAILED,
                mode=ProcessMode.HANDOFF,
                details={"error": "Album missing artist info"},
            )

        return self._add_artist_for_album(artist_info, best, row, lidarr)

    def _add_artist(
        self, artist: dict, row: CsvRow, lidarr: "LidarrClient"
    ) -> ProcessResult:
        foreign_id = artist.get("foreignArtistId", "")

        # Check existing
        existing = lidarr.find_existing(foreign_id)
        if existing:
            log.info("Artist already in Lidarr: %s", artist.get("artistName", "?"))
            return ProcessResult(
                status=ItemStatus.MONITORING,
                mode=ProcessMode.HANDOFF,
                details={
                    "arr_service": "lidarr",
                    "arr_id": existing["id"],
                    "foreign_id": foreign_id,
                    "title": artist.get("artistName", ""),
                    "already_existed": True,
                    "notes": row.notes,
                },
            )

        try:
            root_folders = lidarr.get(f"{lidarr.api_root}/rootfolder")
            quality_profiles = lidarr.get(f"{lidarr.api_root}/qualityprofile")
            metadata_profiles = lidarr.get(f"{lidarr.api_root}/metadataprofile")

            if not root_folders or not quality_profiles or not metadata_profiles:
                return ProcessResult(
                    status=ItemStatus.FAILED,
                    mode=ProcessMode.HANDOFF,
                    details={"error": "Lidarr missing root folder, quality, or metadata profiles"},
                )

            added = lidarr.add_artist(
                artist,
                quality_profile_id=quality_profiles[0]["id"],
                metadata_profile_id=metadata_profiles[0]["id"],
                root_folder_path=root_folders[0]["path"],
            )
            log.info("Added artist to Lidarr: %s", artist.get("artistName", "?"))
            return ProcessResult(
                status=ItemStatus.MONITORING,
                mode=ProcessMode.HANDOFF,
                details={
                    "arr_service": "lidarr",
                    "arr_id": added["id"],
                    "foreign_id": foreign_id,
                    "title": artist.get("artistName", ""),
                    "notes": row.notes,
                },
            )
        except ServiceError as exc:
            if "already been added" in str(exc).lower():
                return ProcessResult(
                    status=ItemStatus.MONITORING,
                    mode=ProcessMode.HANDOFF,
                    details={
                        "arr_service": "lidarr",
                        "foreign_id": foreign_id,
                        "title": artist.get("artistName", ""),
                        "notes": row.notes,
                    },
                )
            log.error("Lidarr add failed: %s", exc)
            return ProcessResult(
                status=ItemStatus.FAILED,
                mode=ProcessMode.HANDOFF,
                details={"error": str(exc)},
            )

    def _add_artist_for_album(
        self, artist: dict, album: dict, row: CsvRow, lidarr: "LidarrClient"
    ) -> ProcessResult:
        # Lidarr needs the artist first; the album gets monitored through artist monitoring
        result = self._add_artist(artist, row, lidarr)
        # Enrich details with album info
        result.details["album_title"] = album.get("title", "")
        result.details["foreign_album_id"] = album.get("foreignAlbumId", "")
        return result

    def check_status(self, state_entry: StateEntry, services: Services) -> StatusResult:
        arr_id = state_entry.details.get("arr_id")
        if not arr_id or not services.lidarr:
            return StatusResult(
                state_key="",
                current_status=state_entry.status,
                message="Cannot check — Lidarr not configured or no arr_id",
            )

        try:
            artist = services.lidarr.get_by_id(arr_id)
            stats = artist.get("statistics", {})
            total = stats.get("albumCount", 0)
            have = stats.get("trackFileCount", 0)
            pct = stats.get("percentOfTracks", 0)

            if total > 0 and pct >= 100:
                return StatusResult(
                    state_key="",
                    current_status=ItemStatus.COMPLETED,
                    has_file=True,
                    message=f"Complete: {have} tracks",
                )
            return StatusResult(
                state_key="",
                current_status=ItemStatus.MONITORING,
                has_file=False,
                download_progress=pct,
                message=f"Progress: {pct:.0f}% tracks acquired",
            )
        except Exception as exc:
            log.warning("Lidarr status check failed: %s", exc)
            return StatusResult(
                state_key="",
                current_status=state_entry.status,
                message=f"Status check failed: {exc}",
            )
