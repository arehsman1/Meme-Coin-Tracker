"""Lightweight job failure tracking for worker recovery.

Does not replace a durable queue; provides in-process backoff hints
so a single poison job does not spin forever in memory mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class FailureRecord:
    count: int = 0
    last_error: str | None = None
    last_at: datetime | None = None
    cooldown_until: datetime | None = None


@dataclass
class FailureTracker:
    max_attempts: int = 5
    base_cooldown_seconds: float = 30.0
    _records: dict[str, FailureRecord] = field(default_factory=dict)

    def key_for_job(self, job: dict[str, Any]) -> str:
        jid = job.get("id") or job.get("job_id")
        if jid:
            return str(jid)
        return f"{job.get('job_type')}:{job.get('payload')}"

    def should_skip(self, job: dict[str, Any], now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        rec = self._records.get(self.key_for_job(job))
        if not rec:
            return False
        if rec.count >= self.max_attempts:
            return True
        if rec.cooldown_until and now < rec.cooldown_until:
            return True
        return False

    def record_success(self, job: dict[str, Any]) -> None:
        self._records.pop(self.key_for_job(job), None)

    def record_failure(self, job: dict[str, Any], error: str) -> FailureRecord:
        from datetime import timedelta

        key = self.key_for_job(job)
        rec = self._records.get(key) or FailureRecord()
        rec.count += 1
        rec.last_error = error[:500]
        rec.last_at = datetime.now(timezone.utc)
        # Exponential-ish cooldown
        delay = self.base_cooldown_seconds * (2 ** min(rec.count - 1, 6))
        rec.cooldown_until = rec.last_at + timedelta(seconds=delay)
        self._records[key] = rec
        return rec

    def stats(self) -> dict[str, Any]:
        return {
            "tracked_failures": len(self._records),
            "exhausted": sum(
                1 for r in self._records.values() if r.count >= self.max_attempts
            ),
        }
