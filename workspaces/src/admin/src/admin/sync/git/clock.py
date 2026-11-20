"""The current time, for the backup's timestamps and branch names."""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(timezone.utc)
