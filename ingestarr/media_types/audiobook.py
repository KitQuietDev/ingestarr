from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from ..models import (
    CsvRow, ItemStatus, ProcessMode, ProcessResult, SearchResult,
    StateEntry, StatusResult,
)
from .base import DirectHandler

if TYPE_CHECKING:
    from ..services import Services

log = logging.getLogger(__name__)


class AudiobookHandler(DirectHandler):
    media_type = "audiobook"
    size_min_bytes = 50 * 1024 * 1024      # 50 MB
    size_max_bytes = 5 * 1024 * 1024 * 1024  # 5 GB
    format_keywords = ["m4b", "mp3", "m4a", "audiobook"]
    exclude_keywords = [
        "x264", "x265", "720p", "1080p", "2160p", "4k", "uhd",
        "bluray", "webrip", "hdtv",
        "epub", "mobi", "pdf", "azw", "fb2", "djvu",
        "mkv", "avi", "mp4",
    ]

    search_prompt_template = (
        "I'm looking for an audiobook:\n"
        "Title: {title}\n"
        "Author/Narrator: {creator}\n"
        "Year: {year}\n"
        "Notes: {notes}\n\n"
        "Generate 2-3 search queries to find this audiobook on indexer sites. "
        "Include 'audiobook' in at least one query. "
        "Try the author name and 'unabridged' as variations."
    )

    classify_prompt_template = (
        "I'm looking for this audiobook:\n"
        "Title: {title}\n"
        "Author/Narrator: {creator}\n"
        "Year: {year}\n"
        "Notes: {notes}\n\n"
        "Classify each search result below. I want AUDIOBOOKS only — not ebooks, "
        "not video, not software. Prefer m4b format, then mp3.\n\n"
        "{results}"
    )

    def process(self, row: CsvRow, services: Services) -> ProcessResult:
        queries = self._generate_search_queries(row, services)
        all_results: list[SearchResult] = []
        seen_guids: set[str] = set()

        for query in queries:
            try:
                results = services.prowlarr.search(query)
            except Exception as exc:
                log.warning("Prowlarr search failed for '%s': %s", query, exc)
                continue

            for r in results:
                if r.guid not in seen_guids:
                    seen_guids.add(r.guid)
                    all_results.append(r)

            if services.config.search_delay > 0:
                time.sleep(services.config.search_delay)

        filtered = self._pre_filter(all_results, row)
        if not filtered:
            log.info("No results after filtering for '%s'", row.title)
            return ProcessResult(
                status=ItemStatus.NOT_FOUND,
                mode=ProcessMode.DIRECT,
                details={"queries": queries, "raw_results": len(all_results)},
            )

        classifications = self._batch_classify(row, filtered, services)

        best_usenet = None
        best_torrent = None
        uncertain = []

        for cl in classifications:
            idx = cl["index"]
            if idx >= len(filtered):
                continue
            result = filtered[idx]

            if cl["match"] == "yes" and cl["format"] == "audiobook":
                if result.protocol == "usenet" and best_usenet is None:
                    best_usenet = result
                elif result.protocol == "torrent" and best_torrent is None:
                    best_torrent = result
                elif not result.protocol and best_usenet is None:
                    best_usenet = result
            elif cl["match"] == "uncertain":
                uncertain.append((result, cl["reason"]))

        chosen = best_usenet or best_torrent

        if chosen:
            return self._push_download(chosen, row, services)

        if uncertain:
            self._write_review_queue(uncertain, row, services)
            return ProcessResult(
                status=ItemStatus.REVIEW,
                mode=ProcessMode.DIRECT,
                details={"uncertain_count": len(uncertain)},
            )

        return ProcessResult(
            status=ItemStatus.NOT_FOUND,
            mode=ProcessMode.DIRECT,
            details={"queries": queries, "filtered": len(filtered)},
        )

    def _push_download(
        self, result: SearchResult, row: CsvRow, services: Services
    ) -> ProcessResult:
        details: dict = {
            "result_title": result.title,
            "indexer": result.indexer,
            "size_mb": round(result.size_mb, 1),
        }

        try:
            if result.protocol == "usenet" or (
                services.sabnzbd and not result.protocol
            ):
                if services.sabnzbd:
                    nzo_id = services.sabnzbd.add_url(
                        result.download_url, category="audiobooks"
                    )
                    details["download_client"] = "sabnzbd"
                    details["download_id"] = nzo_id
                    log.info("Pushed to SABnzbd: %s", result.title)
                else:
                    return self._push_torrent(result, services, details)
            else:
                return self._push_torrent(result, services, details)
        except Exception as exc:
            log.error("Download push failed for '%s': %s", result.title, exc)
            return ProcessResult(
                status=ItemStatus.FAILED,
                mode=ProcessMode.DIRECT,
                details={**details, "error": str(exc)},
            )

        return ProcessResult(
            status=ItemStatus.GRABBED,
            mode=ProcessMode.DIRECT,
            details=details,
        )

    def _push_torrent(
        self, result: SearchResult, services: Services, details: dict
    ) -> ProcessResult:
        if not services.qbittorrent:
            return ProcessResult(
                status=ItemStatus.FAILED,
                mode=ProcessMode.DIRECT,
                details={**details, "error": "No download client available for torrent"},
            )
        try:
            services.qbittorrent.add_torrent(
                result.download_url,
                category="audiobooks",
                savepath=services.config.audiobooks_download_path,
            )
            details["download_client"] = "qbittorrent"
            log.info("Pushed to qBittorrent: %s", result.title)
        except Exception as exc:
            log.error("qBittorrent push failed: %s", exc)
            return ProcessResult(
                status=ItemStatus.FAILED,
                mode=ProcessMode.DIRECT,
                details={**details, "error": str(exc)},
            )

        return ProcessResult(
            status=ItemStatus.GRABBED,
            mode=ProcessMode.DIRECT,
            details=details,
        )

    def check_status(self, state_entry: StateEntry, services: Services) -> StatusResult:
        details = state_entry.details
        client_name = details.get("download_client", "")
        download_id = details.get("download_id", "")

        if client_name == "sabnzbd" and services.sabnzbd and download_id:
            try:
                history = services.sabnzbd.get_history(limit=100)
                for item in history:
                    if item.get("nzo_id") == download_id:
                        if item.get("status") == "Completed":
                            return StatusResult(
                                state_key="",
                                current_status=ItemStatus.COMPLETED,
                                has_file=True,
                                message="Download completed",
                            )
                        return StatusResult(
                            state_key="",
                            current_status=ItemStatus.GRABBED,
                            message=f"Status: {item.get('status', 'unknown')}",
                        )
                queue = services.sabnzbd.get_queue()
                for item in queue:
                    if item.get("nzo_id") == download_id:
                        pct = item.get("percentage", "?")
                        return StatusResult(
                            state_key="",
                            current_status=ItemStatus.GRABBED,
                            download_progress=float(pct) if pct != "?" else None,
                            message=f"Downloading: {pct}%",
                        )
            except Exception as exc:
                log.warning("Status check failed: %s", exc)

        return StatusResult(
            state_key="",
            current_status=state_entry.status,
            message="Status unknown",
        )
