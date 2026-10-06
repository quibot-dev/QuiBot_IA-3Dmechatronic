from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


class SessionLogWriter:
    def __init__(self, log_dir: Optional[Path]) -> None:
        self.path: Optional[Path] = None
        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            self.path = log_dir / f"session-{timestamp}.log"

    def write(self, channel: str, message: str) -> None:
        if self.path is None:
            return

        stamp = datetime.now(timezone.utc).isoformat()
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} [{channel}] {message}\n")

