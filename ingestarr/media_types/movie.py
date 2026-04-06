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

log = logging.getLogger(__name__)


class MovieHandler(HandoffHandler):
    media_type = "movie"
    arr_service_name = "radarr"

    identity_prompt_template = (
        "I'm looking for a movie:\n"
        "Title: {title}\n"
        "Director: {creator}\n"
        "Year: {year}\n"
        "Notes: {notes}\n\n"
        "What is the exact canonical title of this movie? "
        "If multiple movies share this name, use the year and director to disambiguate."
    )

    def process(self, row: CsvRow, services: Services) -> ProcessResult:
        services.require_arr("radarr")
        radarr = services.radarr
        assert radarr is not None

        # Resolve identity via LLM
        search_term = self._resolve_identity(row, services)
        log.info("Movie identity resolved: '%s' → '%s'", row.title, search_term)

        # Check if already in Radarr
        try:
            lookup_results = radarr.lookup(search_term)
        except ServiceError as exc:
            log.error("Radarr lookup failed: %s", exc)
            return ProcessResult(
                status=ItemStatus.FAILED,
                mode=ProcessMode.HANDOFF,
                details={"error": str(exc)},
            )

        if not lookup_results:
            log.info("No Radarr lookup results for '%s'", search_term)
            return ProcessResult(
                status=ItemStatus.NOT_FOUND,
                mode=ProcessMode.HANDOFF,
                details={"search_term": search_term},
            )

        # Pick best match (first result, possibly filtered by year)
        best = lookup_results[0]
        if row.year:
            for r in lookup_results:
                if str(r.get("year", "")) == row.year:
                    best = r
                    break

        tmdb_id = best.get("tmdbId")

        # Check if already exists in library
        existing = radarr.find_existing(tmdb_id)
        if existing:
            existing_id = existing["id"]
            has_file = existing.get("hasFile", False)

            if has_file:
                log.info("Movie already acquired in Radarr: %s", best["title"])
                return ProcessResult(
                    status=ItemStatus.COMPLETED,
                    mode=ProcessMode.HANDOFF,
                    details={
                        "arr_service": "radarr",
                        "arr_id": existing_id,
                        "tmdb_id": tmdb_id,
                        "title": best["title"],
                        "already_existed": True,
                        "notes": row.notes,
                    },
                )

            if not existing.get("monitored", False):
                log.info("Movie exists but unmonitored, enabling: %s", best["title"])
                radarr.set_monitored(existing_id, monitored=True)
                radarr.trigger_search([existing_id], "MoviesSearch")

            return ProcessResult(
                status=ItemStatus.MONITORING,
                mode=ProcessMode.HANDOFF,
                details={
                    "arr_service": "radarr",
                    "arr_id": existing_id,
                    "tmdb_id": tmdb_id,
                    "title": best["title"],
                    "already_existed": True,
                    "notes": row.notes,
                },
            )

        # Add new movie
        try:
            # Get root folder and quality profile from Radarr
            root_folders = radarr.get(f"{radarr.api_root}/rootfolder")
            quality_profiles = radarr.get(f"{radarr.api_root}/qualityprofile")
            if not root_folders or not quality_profiles:
                return ProcessResult(
                    status=ItemStatus.FAILED,
                    mode=ProcessMode.HANDOFF,
                    details={"error": "Radarr has no root folders or quality profiles configured"},
                )

            added = radarr.add_movie(
                best,
                quality_profile_id=quality_profiles[0]["id"],
                root_folder_path=root_folders[0]["path"],
            )
            log.info("Added to Radarr: %s (tmdb=%s)", best["title"], tmdb_id)
            return ProcessResult(
                status=ItemStatus.MONITORING,
                mode=ProcessMode.HANDOFF,
                details={
                    "arr_service": "radarr",
                    "arr_id": added["id"],
                    "tmdb_id": tmdb_id,
                    "title": best["title"],
                    "notes": row.notes,
                },
            )
        except ServiceError as exc:
            if "already been added" in str(exc).lower():
                log.info("Movie already in Radarr (race condition): %s", best["title"])
                return ProcessResult(
                    status=ItemStatus.MONITORING,
                    mode=ProcessMode.HANDOFF,
                    details={
                        "arr_service": "radarr",
                        "tmdb_id": tmdb_id,
                        "title": best["title"],
                        "notes": row.notes,
                    },
                )
            log.error("Radarr add failed: %s", exc)
            return ProcessResult(
                status=ItemStatus.FAILED,
                mode=ProcessMode.HANDOFF,
                details={"error": str(exc)},
            )

    def check_status(self, state_entry: StateEntry, services: Services) -> StatusResult:
        arr_id = state_entry.details.get("arr_id")
        if not arr_id or not services.radarr:
            return StatusResult(
                state_key="",
                current_status=state_entry.status,
                message="Cannot check — Radarr not configured or no arr_id",
            )

        try:
            movie = services.radarr.get_by_id(arr_id)
            has_file = movie.get("hasFile", False)
            return StatusResult(
                state_key="",
                current_status=ItemStatus.COMPLETED if has_file else ItemStatus.MONITORING,
                has_file=has_file,
                message=f"{'Acquired' if has_file else 'Searching'}: {movie.get('title', '?')}",
            )
        except Exception as exc:
            log.warning("Radarr status check failed: %s", exc)
            return StatusResult(
                state_key="",
                current_status=state_entry.status,
                message=f"Status check failed: {exc}",
            )
