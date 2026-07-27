"""Metrik-Grundstruktur.

Prozessinterne Zaehler und Histogramme ohne externe Abhaengigkeit. Die Struktur
ist so gewaehlt, dass ein Prometheus-Export spaeter ergaenzt werden kann, ohne
die Aufrufstellen zu aendern (siehe Roadmap).
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass
class Histogram:
    """Einfache Laufzeitverteilung."""

    count: int = 0
    total: float = 0.0
    minimum: float | None = None
    maximum: float | None = None

    def observe(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)

    @property
    def average(self) -> float:
        return self.total / self.count if self.count else 0.0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "count": self.count,
            "average": round(self.average, 4),
            "min": round(self.minimum, 4) if self.minimum is not None else 0.0,
            "max": round(self.maximum, 4) if self.maximum is not None else 0.0,
        }


@dataclass
class MetricsRegistry:
    """Prozessweite Metriken. Threadsicher, damit Worker und API sie teilen koennen."""

    counters: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    histograms: dict[str, Histogram] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self.counters[name] += amount

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            if name not in self.histograms:
                self.histograms[name] = Histogram()
            self.histograms[name].observe(value)

    @contextmanager
    def timer(self, name: str) -> Iterator[None]:
        """Laufzeit eines Blocks in Sekunden erfassen."""
        started = time.perf_counter()
        try:
            yield
        finally:
            self.observe(name, time.perf_counter() - started)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": dict(self.counters),
                "histograms": {
                    name: histogram.as_dict() for name, histogram in self.histograms.items()
                },
            }

    def reset(self) -> None:
        with self._lock:
            self.counters.clear()
            self.histograms.clear()


#: Prozessweite Registry.
metrics = MetricsRegistry()

# Namen der erfassten Metriken, zentral definiert um Tippfehler zu vermeiden.
ANALYSES_TOTAL = "analyses_total"
ANALYSES_FAILED = "analyses_failed"
SIGNALS_CREATED = "signals_created"
SIGNALS_DISPATCHED = "signals_dispatched"
SIGNALS_SUPPRESSED = "signals_suppressed"
MARKET_DATA_REQUESTS = "market_data_requests"
LLM_CALLS = "llm_calls"
LLM_VALIDATION_FAILURES = "llm_validation_failures"
ANALYSIS_DURATION_SECONDS = "analysis_duration_seconds"
