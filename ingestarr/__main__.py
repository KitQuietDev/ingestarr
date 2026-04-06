from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import load_config
from .services import Services
from .state import StateStore


def setup_logging(config) -> None:
    config.log_dir.mkdir(parents=True, exist_ok=True)

    from datetime import datetime

    log_file = config.log_dir / f"ingestarr_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def cmd_watch(args, config, services, state) -> None:
    from .watcher import Watcher

    watcher = Watcher(config, services, state)
    watcher.run()


def cmd_process(args, config, services, state) -> None:
    from .processor import Processor

    processor = Processor(config, services, state)

    if args.file:
        csv_path = Path(args.file)
        if not csv_path.exists():
            print(f"File not found: {csv_path}", file=sys.stderr)
            sys.exit(1)
        stats = processor.process_csv(csv_path)
        if not args.dry_run:
            processor._move_to_processed(csv_path)
    else:
        stats = processor.process_all()

    print(f"\nResults: {stats}")


def cmd_status(args, config, services, state) -> None:
    from .processor import Processor

    processor = Processor(config, services, state)
    results = processor.check_status(pending_only=args.pending)

    if not results:
        print("No tracked items.")
        return

    # Group by status
    by_status: dict[str, list] = {}
    for r in results:
        status = r.get("current_status", r["status"])
        by_status.setdefault(status, []).append(r)

    for status, items in sorted(by_status.items()):
        print(f"\n=== {status.upper()} ({len(items)}) ===")
        for item in items:
            key = item["key"]
            parts = key.split("|")
            media_type = parts[0] if parts else "?"
            title = parts[1] if len(parts) > 1 else key
            msg = item.get("message", "")
            progress = item.get("progress")
            notes = item.get("details", {}).get("notes", "")

            line = f"  [{media_type}] {title}"
            if msg:
                line += f" — {msg}"
            if progress is not None:
                line += f" ({progress:.0f}%)"
            if notes:
                line += f"  [notes: {notes}]"
            print(line)


def cmd_validate(args, config, services, state) -> None:
    from .csv_parser import load_csv, validate_csv

    csv_path = Path(args.file)
    rows, errors = validate_csv(csv_path)

    if errors:
        print("Validation errors:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Valid: {len(rows)} rows")

    # Summary by type
    from collections import Counter

    type_counts = Counter(r.type.value for r in rows)
    for t, count in sorted(type_counts.items()):
        print(f"  {t}: {count}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ingestarr",
        description="Media intake and routing for *arr stacks",
    )
    parser.add_argument(
        "--env-file", default=None, help="Path to .env file"
    )

    subparsers = parser.add_subparsers(dest="command")

    # watch
    subparsers.add_parser("watch", help="Watch input folder for new CSVs (service mode)")

    # process
    p_process = subparsers.add_parser("process", help="Process CSV file(s) and exit")
    p_process.add_argument("file", nargs="?", default=None, help="CSV file to process")
    p_process.add_argument("--dry-run", action="store_true", help="Validate only, no API calls")

    # status
    p_status = subparsers.add_parser("status", help="Check status of tracked items")
    p_status.add_argument("--pending", action="store_true", help="Show only pending/monitoring items")

    # validate
    p_validate = subparsers.add_parser("validate", help="Validate CSV format")
    p_validate.add_argument("file", help="CSV file to validate")

    args = parser.parse_args()

    if not args.command:
        # Default to watch mode
        args.command = "watch"

    config = load_config(args.env_file)
    setup_logging(config)

    log = logging.getLogger("ingestarr")
    log.info("IngestArr starting (command=%s)", args.command)

    services = Services(config)

    # Validate required services at startup
    errors = services.validate_startup()
    if errors:
        for e in errors:
            log.error("Configuration error: %s", e)
        print(f"Fatal: {len(errors)} configuration error(s). Check logs.", file=sys.stderr)
        sys.exit(1)

    log.info(services.summary())

    state = StateStore(config)

    commands = {
        "watch": cmd_watch,
        "process": cmd_process,
        "status": cmd_status,
        "validate": cmd_validate,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args, config, services, state)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
