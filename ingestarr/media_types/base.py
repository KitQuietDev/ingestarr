from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from ..models import CsvRow, ProcessResult, StateEntry, StatusResult

# Forward references to avoid circular imports
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..services import Services


log = logging.getLogger(__name__)


class HandoffHandler(ABC):
    """Base for media types handled by *arr apps (movies, TV, music).

    The handler resolves the item's identity via LLM, then adds it to the
    appropriate *arr app and lets the *arr handle search/download.
    """

    media_type: str = ""
    arr_service_name: str = ""  # e.g. "radarr", "sonarr"

    # LLM prompt template for identity resolution.
    # Subclasses must provide {title}, {creator}, {year}, {notes} placeholders.
    identity_prompt_template: str = ""

    @abstractmethod
    def process(self, row: CsvRow, services: Services) -> ProcessResult:
        ...

    @abstractmethod
    def check_status(self, state_entry: StateEntry, services: Services) -> StatusResult:
        ...

    def _resolve_identity(self, row: CsvRow, services: Services) -> str:
        """Ask the LLM to resolve a fuzzy title to a canonical search term."""
        prompt = self.identity_prompt_template.format(
            title=row.title,
            creator=row.creator,
            year=row.year,
            notes=row.notes,
        )
        system = (
            "You are a media identification assistant. Given a title and optional metadata, "
            "determine the most likely specific item the user wants. Respond with ONLY the "
            "canonical title that would yield the best search results in a media database. "
            "If the year or creator helps disambiguate, include them in your reasoning but "
            "respond with just the title."
        )
        return services.ollama.complete(prompt, max_tokens=200, system=system).strip()


class DirectHandler(ABC):
    """Base for media types acquired directly via Prowlarr (books, audiobooks).

    The handler searches Prowlarr, pre-filters by size/format, uses the LLM
    to batch-classify results, then pushes the best match to a download client.
    """

    media_type: str = ""
    size_min_bytes: int = 0
    size_max_bytes: int = 0
    format_keywords: list[str] = []
    exclude_keywords: list[str] = []

    # LLM prompt templates — subclasses provide these
    classify_prompt_template: str = ""
    search_prompt_template: str = ""

    @abstractmethod
    def process(self, row: CsvRow, services: Services) -> ProcessResult:
        ...

    @abstractmethod
    def check_status(self, state_entry: StateEntry, services: Services) -> StatusResult:
        ...

    def _pre_filter(self, results: list, row: CsvRow) -> list:
        """Remove results that are obviously wrong by size or keywords."""
        from ..models import SearchResult

        filtered = []
        for r in results:
            if not isinstance(r, SearchResult):
                continue
            # Size bounds
            if self.size_min_bytes and r.size_bytes < self.size_min_bytes:
                continue
            if self.size_max_bytes and r.size_bytes > self.size_max_bytes:
                continue
            # Exclude keywords in title
            title_lower = r.title.lower()
            if any(kw in title_lower for kw in self.exclude_keywords):
                continue
            filtered.append(r)

        log.debug(
            "Pre-filter for '%s': %d → %d results",
            row.title, len(results), len(filtered),
        )
        return filtered

    def _generate_search_queries(self, row: CsvRow, services: Services) -> list[str]:
        """Ask LLM to generate 2-3 search query variations."""
        prompt = self.search_prompt_template.format(
            title=row.title,
            creator=row.creator,
            year=row.year,
            notes=row.notes,
        )
        system = (
            "You are a search query generator for finding media on indexer sites. "
            "Given a title and metadata, generate 2-3 search query variations that "
            "would find the item. Return one query per line, nothing else. "
            "Start with the most specific query and get broader."
        )
        raw = services.ollama.complete(prompt, max_tokens=150, system=system)
        queries = [q.strip() for q in raw.strip().splitlines() if q.strip()]
        # Always include a basic title+creator query as fallback
        basic = row.title
        if row.creator:
            basic = f"{row.title} {row.creator}"
        if basic not in queries:
            queries.append(basic)
        return queries[:3]

    def _batch_classify(
        self, row: CsvRow, results: list, services: Services
    ) -> list[dict]:
        """Send up to 10 results to the LLM in one prompt for classification.

        Returns a list of dicts: {"index": int, "match": "yes"|"no"|"uncertain",
        "format": str, "reason": str}
        """
        from ..models import SearchResult

        if not results:
            return []

        batch = results[:10]
        results_text = "\n".join(
            f"[{i+1}] {r.title} | {r.size_mb:.1f} MB | {r.indexer} | "
            f"{'seeds: ' + str(r.seeders) if r.seeders else 'usenet'}"
            for i, r in enumerate(batch)
            if isinstance(r, SearchResult)
        )

        prompt = self.classify_prompt_template.format(
            title=row.title,
            creator=row.creator,
            year=row.year,
            notes=row.notes,
            results=results_text,
        )
        system = (
            "You are a media classification assistant. For each numbered search result, "
            "determine if it matches the target item. Respond with one line per result in "
            "this exact format:\n"
            "[N] MATCH:yes|no|uncertain FORMAT:ebook|audiobook|video|music|software|unknown "
            "REASON:brief explanation\n"
            "Be strict: wrong author, wrong title, wrong media type = no. "
            "If the user's notes express format preferences, factor those in."
        )
        raw = services.ollama.complete(prompt, max_tokens=500, system=system)

        classifications = []
        for line in raw.strip().splitlines():
            line = line.strip()
            if not line or not line.startswith("["):
                continue
            try:
                entry = self._parse_classification_line(line)
                if entry:
                    classifications.append(entry)
            except Exception:
                log.debug("Failed to parse classification line: %s", line)
                continue

        return classifications

    def _write_review_queue(
        self,
        uncertain: list[tuple],
        row: CsvRow,
        services: Services,
    ) -> None:
        """Append uncertain matches to the review queue CSV."""
        import csv

        review_path = services.config.review_dir / "review_queue.csv"
        review_path.parent.mkdir(parents=True, exist_ok=True)
        file_exists = review_path.exists()

        with open(review_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow([
                    "title", "creator", "year", "result_title",
                    "indexer", "size_mb", "seeds", "reason",
                ])
            for result, reason in uncertain:
                writer.writerow([
                    row.title, row.creator, row.year, result.title,
                    result.indexer, f"{result.size_mb:.1f}", result.seeders, reason,
                ])

    @staticmethod
    def _parse_classification_line(line: str) -> dict | None:
        """Parse a single classification line like:
        [1] MATCH:yes FORMAT:ebook REASON:exact title match
        """
        import re

        idx_match = re.match(r"\[(\d+)\]", line)
        if not idx_match:
            return None

        index = int(idx_match.group(1)) - 1  # 0-based

        match_m = re.search(r"MATCH:\s*(yes|no|uncertain)", line, re.IGNORECASE)
        format_m = re.search(r"FORMAT:\s*(\S+)", line, re.IGNORECASE)
        reason_m = re.search(r"REASON:\s*(.+)", line, re.IGNORECASE)

        return {
            "index": index,
            "match": match_m.group(1).lower() if match_m else "uncertain",
            "format": format_m.group(1).lower() if format_m else "unknown",
            "reason": reason_m.group(1).strip() if reason_m else "",
        }
