from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone

from .config import Config
from .models import ItemStatus, ProcessMode, ProcessResult, StateEntry

log = logging.getLogger(__name__)

_STATE_VERSION = 1


class StateStore:
    def __init__(self, config: Config):
        self._path = config.state_dir / "state.json"
        self._data: dict[str, StateEntry] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for key, val in raw.get("items", {}).items():
                self._data[key] = StateEntry(
                    status=ItemStatus(val["status"]),
                    mode=ProcessMode(val["mode"]),
                    source_csv=val.get("source_csv", ""),
                    timestamp=val.get("timestamp", ""),
                    details=val.get("details", {}),
                )
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            log.error("Corrupt state file %s: %s — starting fresh", self._path, exc)
            self._data = {}

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _STATE_VERSION,
            "items": {
                key: {
                    "status": entry.status.value,
                    "mode": entry.mode.value,
                    "source_csv": entry.source_csv,
                    "timestamp": entry.timestamp,
                    "details": entry.details,
                }
                for key, entry in self._data.items()
            },
        }
        # Atomic write: temp file + rename
        fd, tmp = tempfile.mkstemp(
            dir=str(self._path.parent), suffix=".tmp", prefix="state_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, str(self._path))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def is_processed(self, state_key: str) -> bool:
        entry = self._data.get(state_key)
        if entry is None:
            return False
        return entry.status not in (ItemStatus.FAILED,)

    def record(self, state_key: str, result: ProcessResult, source_csv: str) -> None:
        self._data[state_key] = StateEntry(
            status=result.status,
            mode=result.mode,
            source_csv=source_csv,
            timestamp=datetime.now(timezone.utc).isoformat(),
            details=result.details,
        )
        self.save()

    def get(self, state_key: str) -> StateEntry | None:
        return self._data.get(state_key)

    def all_items(self) -> dict[str, StateEntry]:
        return dict(self._data)

    def items_by_status(self, *statuses: ItemStatus) -> dict[str, StateEntry]:
        return {
            k: v for k, v in self._data.items() if v.status in statuses
        }
