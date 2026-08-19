"""Webhook monitoring and health checks for billing providers.

Tracks webhook success/failure rates, latency, and provides health endpoints
for operational monitoring.
"""

import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


class WebhookMetrics:
    """Metrics for webhook processing."""

    def __init__(self) -> None:
        self._counts: dict[str, dict[str, int]] = defaultdict(
            lambda: {"received": 0, "processed": 0, "failed": 0, "idempotent_skip": 0}
        )
        self._latencies: dict[str, list[float]] = defaultdict(list)
        self._errors: dict[str, list[tuple[datetime, str]]] = defaultdict(list)

    def record_received(self, provider: str) -> None:
        """Record a webhook received from provider."""
        self._counts[provider]["received"] += 1

    def record_processed(self, provider: str, latency_ms: float) -> None:
        """Record a successfully processed webhook."""
        self._counts[provider]["processed"] += 1
        self._latencies[provider].append(latency_ms)
        # Keep only last 100 latencies
        if len(self._latencies[provider]) > 100:
            self._latencies[provider].pop(0)

    def record_failed(self, provider: str, error: str) -> None:
        """Record a failed webhook."""
        self._counts[provider]["failed"] += 1
        self._errors[provider].append((datetime.now(UTC), error))
        # Keep only last 50 errors
        if len(self._errors[provider]) > 50:
            self._errors[provider].pop(0)

    def record_idempotent_skip(self, provider: str) -> None:
        """Record an idempotent skip (duplicate webhook)."""
        self._counts[provider]["idempotent_skip"] += 1

    def get_stats(self, provider: str) -> dict[str, Any]:
        """Get statistics for a specific provider."""
        counts = self._counts.get(provider, {})
        latencies = self._latencies.get(provider, [])
        errors = self._errors.get(provider, [])

        success_rate = 0.0
        if counts["received"] > 0:
            success_rate = (counts["processed"] / counts["received"]) * 100

        avg_latency = 0.0
        if latencies:
            avg_latency = sum(latencies) / len(latencies)

        recent_errors = []
        now = datetime.now(UTC)
        for ts, msg in errors:
            if now - ts < timedelta(hours=1):
                recent_errors.append(f"{ts.isoformat()}: {msg}")

        return {
            "provider": provider,
            "received": counts["received"],
            "processed": counts["processed"],
            "failed": counts["failed"],
            "idempotent_skips": counts["idempotent_skip"],
            "success_rate_percent": round(success_rate, 2),
            "avg_latency_ms": round(avg_latency, 2),
            "recent_errors_count": len(recent_errors),
            "recent_errors": recent_errors[-10:],  # Last 10 recent errors
        }

    def get_all_stats(self) -> dict[str, dict[str, Any]]:
        """Get statistics for all providers."""
        return {
            provider: self.get_stats(provider)
            for provider in ["paddle", "yookassa", "prodamus"]
        }

    def check_health(self, provider: str) -> dict[str, Any]:
        """Check if webhook processing is healthy for a provider."""
        stats = self.get_stats(provider)

        # Health criteria
        is_healthy = True
        issues = []

        # Success rate should be > 95%
        if stats["received"] >= 10:  # Only check if we have enough data
            if stats["success_rate_percent"] < 95:
                is_healthy = False
                issues.append(f"Low success rate: {stats['success_rate_percent']}%")

            # Recent errors should be < 5 in last hour
            if stats["recent_errors_count"] > 5:
                is_healthy = False
                issues.append(f"Too many recent errors: {stats['recent_errors_count']}")

        return {
            "provider": provider,
            "healthy": is_healthy,
            "issues": issues,
            "stats": stats,
        }


# Global metrics instance
_webhook_metrics = WebhookMetrics()


def get_webhook_metrics() -> WebhookMetrics:
    """Get the global webhook metrics instance."""
    return _webhook_metrics


def check_webhook_health() -> dict[str, Any]:
    """Check health of all webhook providers."""
    metrics = get_webhook_metrics()
    overall_healthy = True

    provider_health = {}
    for provider in ["paddle", "yookassa", "prodamus"]:
        health = metrics.check_health(provider)
        provider_health[provider] = health
        if not health["healthy"]:
            overall_healthy = False

    return {
        "overall_healthy": overall_healthy,
        "providers": provider_health,
        "timestamp": datetime.now(UTC).isoformat(),
    }
