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


class BookHandler(DirectHandler):
    media_type = "book"
    size_min_bytes = 50 * 1024          # 50 KB
    size_max_bytes = 500 * 1024 * 1024  # 500 MB
    format_keywords = ["epub", "mobi", "pdf", "azw", "azw3", "fb2", "djvu"]
    exclude_keywords = [
        "x264", "x265", "720p", "1080p", "2160p", "4k", "uhd",
        "s01", "s02", "s03", "s04", "s05", "e01", "e02",
        "bluray", "webrip", "hdtv", "aac", "ac3", "dts",
        "mp4", "mkv", "avi", "flac", "mp3", "m4b",
    ]

    search_prompt_template = (
        "I'm looking for an ebook:\n"
        "Title: {title}\n"
        "Author: {creator}\n"
        "Year: {year}\n"
        "Notes: {notes}\n\n"
        "Generate 2-3 search queries to find this ebook on indexer sites. "
        "Include the author name in at least one query. "
        "If it's part of a series, try both the book title alone and with the series name."
    )

    classify_prompt_template = (
        "I'm looking for this ebook:\n"
        "Title: {title}\n"
        "Author: {creator}\n"
        "Year: {year}\n"
        "Notes: {notes}\n\n"
        "Classify each search result below. I want EBOOKS only — not audiobooks, "
        "not videos, not software. Prefer epub format if the user hasn't specified otherwise.\n\n"
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

        # Find best match: YES + ebook format
        best_usenet = None
        best_torrent = None
        uncertain = []

        for cl in classifications:
            idx = cl["index"]
            if idx >= len(filtered):
                continue
            result = filtered[idx]

            if cl["match"] == "yes" and cl["format"] == "ebook":
                if result.protocol == "usenet" and best_usenet is None:
                    best_usenet = result
                elif result.protocol == "torrent" and best_torrent is None:
                    best_torrent = result
                elif not result.protocol:
                    # Unknown protocol — treat as usenet if has download URL
                    if best_usenet is None:
                        best_usenet = result
            elif cl["match"] == "uncertain":
                uncertain.append((result, cl["reason"]))

        # Prefer usenet, fallback to torrent
        chosen = best_usenet or best_torrent

        if chosen:
            return self._push_download(chosen, row, services)

        # No confirmed match — check uncertains
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
                        result.download_url, category="ebooks"
                    )
                    details["download_client"] = "sabnzbd"
                    details["download_id"] = nzo_id
                    log.info("Pushed to SABnzbd: %s", result.title)
                else:
                    log.warning("No SABnzbd configured, trying qBittorrent fallback")
                    return self._push_torrent(result, row, services, details)
            else:
                return self._push_torrent(result, row, services, details)
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
        self,
        result: SearchResult,
        row: CsvRow,
        services: Services,
        details: dict,
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
                category="ebooks",
                savepath=services.config.books_download_path,
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
            # Check SABnzbd history for completion
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
                # Check active queue
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
