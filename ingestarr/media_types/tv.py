from __future__ import annotations

import logging
import re
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


def parse_seasons(season_str: str) -> list[int] | None:
    """Parse the Season column.

    Returns None for "all"/blank (= monitor all), or a list of ints.
    Supports: "1", "1-3", "1,3,5", "all", "".
    """
    if not season_str or season_str.strip().lower() == "all":
        return None

    seasons: set[int] = set()
    for part in season_str.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            try:
                for s in range(int(start.strip()), int(end.strip()) + 1):
                    seasons.add(s)
            except ValueError:
                log.warning("Invalid season range: '%s'", part)
        else:
            try:
                seasons.add(int(part))
            except ValueError:
                log.warning("Invalid season number: '%s'", part)

    return sorted(seasons) if seasons else None


class TvHandler(HandoffHandler):
    media_type = "tv"
    arr_service_name = "sonarr"

    identity_prompt_template = (
        "I'm looking for a TV series:\n"
        "Title: {title}\n"
        "Creator: {creator}\n"
        "Year: {year}\n"
        "Notes: {notes}\n\n"
        "What is the exact canonical title of this TV series? "
        "If multiple shows share this name (e.g., 'The Office' UK vs US), "
        "use the year and creator to disambiguate."
    )

    def process(self, row: CsvRow, services: Services) -> ProcessResult:
        services.require_arr("sonarr")
        sonarr = services.sonarr
        assert sonarr is not None

        seasons = parse_seasons(row.season)

        search_term = self._resolve_identity(row, services)
        log.info("TV identity resolved: '%s' → '%s'", row.title, search_term)

        try:
            lookup_results = sonarr.lookup(search_term)
        except ServiceError as exc:
            log.error("Sonarr lookup failed: %s", exc)
            return ProcessResult(
                status=ItemStatus.FAILED,
                mode=ProcessMode.HANDOFF,
                details={"error": str(exc)},
            )

        if not lookup_results:
            log.info("No Sonarr lookup results for '%s'", search_term)
            return ProcessResult(
                status=ItemStatus.NOT_FOUND,
                mode=ProcessMode.HANDOFF,
                details={"search_term": search_term},
            )

        best = lookup_results[0]
        if row.year:
            for r in lookup_results:
                if str(r.get("year", "")) == row.year:
                    best = r
                    break

        tvdb_id = best.get("tvdbId")

        # Check if already exists
        existing = sonarr.find_existing(tvdb_id)
        if existing:
            existing_id = existing["id"]

            # Check season status
            has_all = all(
                ep.get("hasFile", False)
                for ep in existing.get("episodes", [])
            ) if existing.get("episodes") else False

            if not existing.get("monitored", False):
                log.info("Series exists but unmonitored, enabling: %s", best["title"])
                sonarr.set_monitored(existing_id, monitored=True)
                sonarr.trigger_search([existing_id], "SeriesSearch")

            return ProcessResult(
                status=ItemStatus.COMPLETED if has_all else ItemStatus.MONITORING,
                mode=ProcessMode.HANDOFF,
                details={
                    "arr_service": "sonarr",
                    "arr_id": existing_id,
                    "tvdb_id": tvdb_id,
                    "title": best["title"],
                    "seasons_requested": seasons,
                    "already_existed": True,
                    "notes": row.notes,
                },
            )

        # Add new series
        try:
            root_folders = sonarr.get(f"{sonarr.api_root}/rootfolder")
            quality_profiles = sonarr.get(f"{sonarr.api_root}/qualityprofile")
            if not root_folders or not quality_profiles:
                return ProcessResult(
                    status=ItemStatus.FAILED,
                    mode=ProcessMode.HANDOFF,
                    details={"error": "Sonarr has no root folders or quality profiles configured"},
                )

            added = sonarr.add_series(
                best,
                quality_profile_id=quality_profiles[0]["id"],
                root_folder_path=root_folders[0]["path"],
                seasons=seasons,
            )
            log.info(
                "Added to Sonarr: %s (tvdb=%s, seasons=%s)",
                best["title"], tvdb_id, seasons or "all",
            )
            return ProcessResult(
                status=ItemStatus.MONITORING,
                mode=ProcessMode.HANDOFF,
                details={
                    "arr_service": "sonarr",
                    "arr_id": added["id"],
                    "tvdb_id": tvdb_id,
                    "title": best["title"],
                    "seasons_requested": seasons,
                    "notes": row.notes,
                },
            )
        except ServiceError as exc:
            if "already been added" in str(exc).lower():
                log.info("Series already in Sonarr (race condition): %s", best["title"])
                return ProcessResult(
                    status=ItemStatus.MONITORING,
                    mode=ProcessMode.HANDOFF,
                    details={
                        "arr_service": "sonarr",
                        "tvdb_id": tvdb_id,
                        "title": best["title"],
                        "seasons_requested": seasons,
                        "notes": row.notes,
                    },
                )
            log.error("Sonarr add failed: %s", exc)
            return ProcessResult(
                status=ItemStatus.FAILED,
                mode=ProcessMode.HANDOFF,
                details={"error": str(exc)},
            )

    def check_status(self, state_entry: StateEntry, services: Services) -> StatusResult:
        arr_id = state_entry.details.get("arr_id")
        if not arr_id or not services.sonarr:
            return StatusResult(
                state_key="",
                current_status=state_entry.status,
                message="Cannot check — Sonarr not configured or no arr_id",
            )

        try:
            series = services.sonarr.get_by_id(arr_id)
            stats = series.get("statistics", {})
            pct = stats.get("percentOfEpisodes", 0)
            total = stats.get("episodeCount", 0)
            have = stats.get("episodeFileCount", 0)

            if total > 0 and have >= total:
                return StatusResult(
                    state_key="",
                    current_status=ItemStatus.COMPLETED,
                    has_file=True,
                    message=f"Complete: {have}/{total} episodes",
                )
            return StatusResult(
                state_key="",
                current_status=ItemStatus.MONITORING,
                has_file=False,
                download_progress=pct,
                message=f"Progress: {have}/{total} episodes ({pct:.0f}%)",
            )
        except Exception as exc:
            log.warning("Sonarr status check failed: %s", exc)
            return StatusResult(
                state_key="",
                current_status=state_entry.status,
                message=f"Status check failed: {exc}",
            )
