"""UTC time helpers used across the application."""
import time
from datetime import datetime, timezone


def now_utc_epoch() -> int:
    """Current UTC time as Unix timestamp (seconds)."""
    return int(time.time())


def epoch_24h_ago() -> int:
    """Unix timestamp for 24 hours ago."""
    return now_utc_epoch() - 86_400


def format_utc(epoch: int) -> str:
    """Format a Unix timestamp as a human-readable UTC string."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
