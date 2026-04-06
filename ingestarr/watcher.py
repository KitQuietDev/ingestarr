from __future__ import annotations

import logging
import time
from pathlib import Path

from inotify_simple import INotify, flags

from .config import Config
from .processor import Processor
from .services import Services
from .state import StateStore

log = logging.getLogger(__name__)


class Watcher:
    """Watch the input directory for new CSV files using inotify.

    Near-zero CPU when idle. Processes items one at a time with configurable delays.
    """

    def __init__(self, config: Config, services: Services, state: StateStore):
        self.config = config
        self.processor = Processor(config, services, state)
        self._inotify = INotify()
        self._watch_dir = config.input_dir

    def run(self) -> None:
        self._watch_dir.mkdir(parents=True, exist_ok=True)

        # Process any existing CSVs first
        log.info("Checking for existing CSVs in %s", self._watch_dir)
        stats = self.processor.process_all()
        if stats.get("files", 0) > 0:
            log.info("Processed %d existing CSV(s): %s", stats["files"], stats)

        # Set up inotify watch
        watch_flags = flags.CLOSE_WRITE | flags.MOVED_TO
        self._inotify.add_watch(str(self._watch_dir), watch_flags)
        log.info("Watching %s for new CSV files...", self._watch_dir)

        try:
            while True:
                events = self._inotify.read()
                for event in events:
                    if event.name and event.name.endswith(".csv"):
                        csv_path = self._watch_dir / event.name
                        log.info("New CSV detected: %s", event.name)

                        # Brief delay for slow writes / scp
                        time.sleep(2)

                        if csv_path.exists():
                            stats = self.processor.process_csv(csv_path)
                            log.info("Finished %s: %s", event.name, stats)
                            # process_all() moves files internally, but the
                            # inotify path processes single files — move explicitly.
                            self.processor._move_to_processed(csv_path)
        except KeyboardInterrupt:
            log.info("Watcher stopped by user")
        finally:
            self._inotify.close()
