from __future__ import annotations

import logging
import signal
import time
from pathlib import Path

from .config import Config
from .csv_parser import load_csv
from .media_types import get_handler
from .models import CsvRow, ItemStatus, ProcessMode, ProcessResult
from .services import Services
from .state import StateStore

log = logging.getLogger(__name__)


class Processor:
    """Core processing engine. Handles one CSV or all CSVs in input dir."""

    def __init__(self, config: Config, services: Services, state: StateStore):
        self.config = config
        self.services = services
        self.state = state
        self._shutdown_requested = False

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        log.info("Shutdown requested (signal %d), finishing current item...", signum)
        self._shutdown_requested = True

    def process_csv(self, path: Path) -> dict:
        """Process a single CSV file. Returns summary stats."""
        rows = load_csv(path)
        stats = {"total": len(rows), "processed": 0, "skipped": 0, "errors": 0}

        for row in rows:
            if self._shutdown_requested:
                log.info("Shutdown: stopping after %d/%d items", stats["processed"], stats["total"])
                break

            state_key = row.state_key
            if self.state.is_processed(state_key):
                log.debug("Skipping already-processed: %s", state_key)
                stats["skipped"] += 1
                continue

            log.info("Processing: [%s] %s — %s", row.type.value, row.title, row.creator)

            try:
                handler = get_handler(row.type.value)
                result = handler.process(row, self.services)
                self.state.record(state_key, result, path.name)
                log.info(
                    "Result: %s — %s (%s)",
                    row.title, result.status.value, result.details.get("title", ""),
                )
                stats["processed"] += 1
            except Exception as exc:
                log.error("Failed to process '%s': %s", row.title, exc, exc_info=True)
                self.state.record(
                    state_key,
                    ProcessResult(
                        status=ItemStatus.FAILED,
                        mode=ProcessMode.DIRECT,
                        details={"error": str(exc)},
                    ),
                    path.name,
                )
                stats["errors"] += 1

            # Delay between items
            if not self._shutdown_requested and self.config.process_delay > 0:
                time.sleep(self.config.process_delay)

        return stats

    def process_all(self) -> dict:
        """Process all CSVs in the input directory."""
        input_dir = self.config.input_dir
        csv_files = sorted(input_dir.glob("*.csv"))

        if not csv_files:
            log.info("No CSV files found in %s", input_dir)
            return {"files": 0}

        total_stats = {"files": len(csv_files), "total": 0, "processed": 0, "skipped": 0, "errors": 0}

        for csv_path in csv_files:
            if self._shutdown_requested:
                break

            log.info("=== Processing %s ===", csv_path.name)
            stats = self.process_csv(csv_path)

            for key in ("total", "processed", "skipped", "errors"):
                total_stats[key] += stats[key]

            # Move to processed/
            self._move_to_processed(csv_path)

        return total_stats

    def _move_to_processed(self, path: Path) -> None:
        processed_dir = self.config.processed_dir
        processed_dir.mkdir(parents=True, exist_ok=True)

        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = processed_dir / f"{path.stem}_{timestamp}{path.suffix}"
        path.rename(dest)
        log.info("Moved %s → %s", path.name, dest.name)

    def check_status(self, pending_only: bool = False) -> list[dict]:
        """Check status of all tracked items. Returns list of status dicts."""
        if pending_only:
            items = self.state.items_by_status(ItemStatus.MONITORING, ItemStatus.GRABBED)
        else:
            items = self.state.all_items()

        results = []
        for key, entry in items.items():
            status_info = {"key": key, "status": entry.status.value, "details": entry.details}

            if entry.status in (ItemStatus.MONITORING, ItemStatus.GRABBED):
                try:
                    media_type = key.split("|")[0]
                    handler = get_handler(media_type)
                    sr = handler.check_status(entry, self.services)
                    status_info["current_status"] = sr.current_status.value
                    status_info["has_file"] = sr.has_file
                    status_info["message"] = sr.message
                    if sr.download_progress is not None:
                        status_info["progress"] = sr.download_progress

                    # Update state if status changed
                    if sr.current_status != entry.status:
                        from .models import ProcessResult
                        self.state.record(
                            key,
                            ProcessResult(
                                status=sr.current_status,
                                mode=entry.mode,
                                details=entry.details,
                            ),
                            entry.source_csv,
                        )
                except Exception as exc:
                    log.warning("Status check failed for %s: %s", key, exc)
                    status_info["message"] = f"Check failed: {exc}"

            results.append(status_info)

        return results
