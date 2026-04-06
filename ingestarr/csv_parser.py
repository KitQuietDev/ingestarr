from __future__ import annotations

import csv
import logging
from pathlib import Path

from .models import CsvRow, MediaType

log = logging.getLogger(__name__)

_VALID_TYPES = {t.value for t in MediaType}

_REQUIRED_COLUMNS = {"type", "title"}
_ALL_COLUMNS = {"type", "title", "creator", "year", "season", "notes"}

_NOTES_MAX_LEN = 200


class CsvError(Exception):
    pass


def _normalize_headers(headers: list[str]) -> list[str]:
    """Lowercase, strip whitespace and BOM from header names."""
    normalized = []
    for h in headers:
        h = h.strip().lower()
        # Strip BOM if present on first header
        if h.startswith("\ufeff"):
            h = h[1:]
        normalized.append(h)
    return normalized


def load_csv(path: Path, source_name: str = "") -> list[CsvRow]:
    """Load and validate a CSV file. Raises CsvError on structural problems."""
    if not path.exists():
        raise CsvError(f"File not found: {path}")

    source = source_name or path.name
    rows: list[CsvRow] = []
    errors: list[str] = []

    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise CsvError(f"Empty or header-only CSV: {path}")

        raw_headers = list(reader.fieldnames)
        norm_headers = _normalize_headers(raw_headers)

        # Build mapping from normalized → original header
        header_map = dict(zip(norm_headers, raw_headers))

        missing = _REQUIRED_COLUMNS - set(norm_headers)
        if missing:
            raise CsvError(
                f"Missing required columns: {', '.join(sorted(missing))}. "
                f"Found: {', '.join(norm_headers)}"
            )

        unknown = set(norm_headers) - _ALL_COLUMNS
        if unknown:
            log.warning("Ignoring unknown columns: %s", ", ".join(sorted(unknown)))

        for line_num, raw_row in enumerate(reader, start=2):
            # Re-key the row using normalized headers
            row_data = {}
            for norm, orig in header_map.items():
                row_data[norm] = (raw_row.get(orig) or "").strip()

            # Validate type
            type_val = row_data.get("type", "").lower()
            if not type_val:
                errors.append(f"Line {line_num}: missing Type")
                continue
            if type_val not in _VALID_TYPES:
                errors.append(
                    f"Line {line_num}: invalid Type '{type_val}' "
                    f"(must be one of: {', '.join(sorted(_VALID_TYPES))})"
                )
                continue

            # Validate title
            title = row_data.get("title", "")
            if not title:
                errors.append(f"Line {line_num}: missing Title")
                continue

            # Validate season (only for tv)
            season = row_data.get("season", "")
            if season and type_val != "tv":
                log.warning(
                    "Line %d: Season column ignored for type '%s'",
                    line_num,
                    type_val,
                )
                season = ""

            # Validate / truncate notes
            notes = row_data.get("notes", "")
            if len(notes) > _NOTES_MAX_LEN:
                log.warning(
                    "Line %d: Notes truncated to %d chars", line_num, _NOTES_MAX_LEN
                )
                notes = notes[:_NOTES_MAX_LEN]

            rows.append(
                CsvRow(
                    type=MediaType(type_val),
                    title=title,
                    creator=row_data.get("creator", ""),
                    year=row_data.get("year", ""),
                    season=season,
                    notes=notes,
                    source_csv=source,
                )
            )

    if errors:
        log.error("CSV validation errors in %s:\n  %s", path, "\n  ".join(errors))
        # Still return valid rows — log the bad ones but don't abort
        log.info("Loaded %d valid rows, %d errors from %s", len(rows), len(errors), path)
    else:
        log.info("Loaded %d rows from %s", len(rows), path)

    return rows


def validate_csv(path: Path) -> tuple[list[CsvRow], list[str]]:
    """Validate without processing. Returns (rows, errors)."""
    errors: list[str] = []

    if not path.exists():
        return [], [f"File not found: {path}"]

    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return [], ["Empty or header-only CSV"]

        norm_headers = _normalize_headers(list(reader.fieldnames))
        missing = _REQUIRED_COLUMNS - set(norm_headers)
        if missing:
            return [], [f"Missing required columns: {', '.join(sorted(missing))}"]

    rows = load_csv(path)
    return rows, errors
