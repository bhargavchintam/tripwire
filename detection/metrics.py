"""Small measurement helpers for the detector: every number we report is measured here.

    from detection.metrics import Timer, RollingSamples, make_heartbeat
    with Timer() as t:
        rows = await ch.query(sql, params)
    samples.add(t.ms)                      # RollingSamples -> p50 / p95 (nearest-rank)
    hb = make_heartbeat("detector", {"funnel": t.ms}, iteration=3, hits=1)
"""

from __future__ import annotations

import math
from collections import deque
from time import perf_counter
from typing import Any

from tripwire.contracts import Heartbeat


class Timer:
    """Context manager measuring wall time in milliseconds (perf_counter)."""

    def __init__(self) -> None:
        self.ms: float = 0.0
        self._t0: float | None = None

    def __enter__(self) -> Timer:
        self._t0 = perf_counter()
        return self

    def __exit__(self, *exc: object) -> bool:
        self.ms = round((perf_counter() - (self._t0 or perf_counter())) * 1000, 3)
        return False


class RollingSamples:
    """The last `maxlen` samples with nearest-rank percentiles (None when empty)."""

    def __init__(self, maxlen: int = 500) -> None:
        self._d: deque[float] = deque(maxlen=maxlen)

    def add(self, value: float) -> None:
        v = float(value)
        if math.isfinite(v):
            self._d.append(v)

    def __len__(self) -> int:
        return len(self._d)

    def percentile(self, p: float) -> float | None:
        if not self._d:
            return None
        xs = sorted(self._d)
        k = max(0, math.ceil(p * len(xs)) - 1)  # nearest-rank
        return xs[k]

    @property
    def p50(self) -> float | None:
        return self.percentile(0.50)

    @property
    def p95(self) -> float | None:
        return self.percentile(0.95)

    @property
    def last(self) -> float | None:
        return self._d[-1] if self._d else None


def make_heartbeat(source: str, timings: dict[str, float] | None, **metrics: Any) -> Heartbeat:
    """Heartbeat for POST /heartbeat: only finite, non-negative timings are kept (ms, 3 decimals)."""
    clean: dict[str, float] = {}
    for name, value in (timings or {}).items():
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v) and v >= 0:
            clean[str(name)] = round(v, 3)
    return Heartbeat(source=source, query_timings_ms=clean, metrics=dict(metrics))
