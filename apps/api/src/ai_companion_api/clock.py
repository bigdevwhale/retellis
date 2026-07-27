"""Strictly monotonic UTC timestamps for in-process stores.

Windows ``datetime.now(UTC)`` resolution is ~1ms; two writes landing in the
same tick get identical timestamps, which makes time-ordered listings
(conversations, journal, audit fields) non-deterministic — a stable sort on a
tied key keeps insertion order regardless of ``reverse=True``. ``utcnow()``
never returns the same value twice within a process (a tie bumps by 1µs), so
in-memory ordering matches Postgres behavior, where each later INSERT commits
with a later ``now()``.

Single-asyncio-loop assumption (same as the in-memory stores): no lock. The
1µs bump keeps the value within real-clock accuracy — it is a tiebreaker, not
a clock replacement.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

_last: datetime | None = None


def utcnow() -> datetime:
    global _last  # noqa: PLW0603 — process-wide tiebreaker state by design
    now = datetime.now(UTC)
    if _last is not None and now <= _last:
        now = _last + timedelta(microseconds=1)
    _last = now
    return now


__all__ = ["utcnow"]
