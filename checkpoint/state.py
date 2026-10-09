"""All in-memory checkpoint state + debounced persistence to var/state.json.

Persisted: blocked set, modes, watermarks, per-agent last ts + last chain hash,
verdict keys, incidents, alerts, recent tool events, ring buffers, hold flag,
policy, heartbeats and measured samples. A restart therefore never silently
unblocks an agent. A corrupt state file is moved aside (never deleted) and the
problem is surfaced through ``load_error`` (shown on /health).

Not persisted: asyncio locks, replay runs.
"""

from __future__ import annotations

import asyncio
import collections
import json
import os
import time
from pathlib import Path
from typing import Any

from loguru import logger

from tripwire.contracts import AgentMode, Incident, Policy

RING_WINDOW_MS = 60_000
RING_MAX = 200
RECENT_EVENTS_MAX = 200
RECENT_ALERTS_MAX = 200
INCIDENTS_MAX = 500
INCIDENT_STEPS_MAX = 200
VERDICT_KEYS_MAX = 10_000
SAMPLES_MAX = 1000
TIMING_SAMPLES_MAX = 2000

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_PATH = ROOT / "var" / "state.json"


def now_ms() -> int:
    return time.time_ns() // 1_000_000


class State:
    def __init__(self, path: Path | str | None = DEFAULT_STATE_PATH) -> None:
        self.path: Path | None = Path(path) if path is not None else None
        self.last_ts: dict[str, int] = {}
        self.last_hash: dict[str, str] = {}
        self.blocked: set[str] = set()
        self.modes: dict[str, AgentMode] = {}
        self.watermarks: dict[str, int] = {}
        self.verdict_keys: dict[str, int] = {}  # ordered set: key -> recorded ms
        self.incidents: dict[str, Incident] = {}  # insertion-ordered
        self.recent_alerts: collections.deque[dict[str, Any]] = collections.deque(maxlen=RECENT_ALERTS_MAX)
        self.recent_events: collections.deque[dict[str, Any]] = collections.deque(maxlen=RECENT_EVENTS_MAX)
        self.rings: dict[str, collections.deque[dict[str, Any]]] = {}
        self.hold_enabled: bool = False
        self.policy: Policy = Policy()
        self.heartbeats: dict[str, dict[str, Any]] = {}
        self.timing_samples: dict[str, collections.deque[float]] = {}
        self.ttd_samples: collections.deque[float] = collections.deque(maxlen=SAMPLES_MAX)
        self.ttc_samples: collections.deque[float] = collections.deque(maxlen=SAMPLES_MAX)
        self.hold_samples: collections.deque[float] = collections.deque(maxlen=SAMPLES_MAX)
        self.load_error: str | None = None
        self.locks: dict[str, asyncio.Lock] = {}
        self.dirty = False
        self.last_saved_ms: int | None = None

    # ---- helpers ----------------------------------------------------------------
    def lock_for(self, agent_id: str) -> asyncio.Lock:
        lk = self.locks.get(agent_id)
        if lk is None:
            lk = self.locks[agent_id] = asyncio.Lock()
        return lk

    def mark_dirty(self) -> None:
        self.dirty = True

    def next_ts(self, agent_id: str, now: int | None = None) -> int:
        now = now_ms() if now is None else now
        ts = max(now, self.last_ts.get(agent_id, 0) + 1)
        self.last_ts[agent_id] = ts
        self.modes.setdefault(agent_id, "normal")
        return ts

    def ring(self, agent_id: str) -> collections.deque[dict[str, Any]]:
        r = self.rings.get(agent_id)
        if r is None:
            r = self.rings[agent_id] = collections.deque(maxlen=RING_MAX)
        return r

    def ring_window(self, agent_id: str, now: int | None = None) -> list[dict[str, Any]]:
        now = now_ms() if now is None else now
        r = self.ring(agent_id)
        while r and r[0]["ts_ms"] < now - RING_WINDOW_MS:
            r.popleft()
        return list(r)

    def push_ring(self, agent_id: str, step: dict[str, Any]) -> None:
        r = self.ring(agent_id)
        r.append(step)
        cutoff = step["ts_ms"] - RING_WINDOW_MS
        while r and r[0]["ts_ms"] < cutoff:
            r.popleft()

    def add_verdict_key(self, key: str) -> None:
        self.verdict_keys[key] = now_ms()
        while len(self.verdict_keys) > VERDICT_KEYS_MAX:
            self.verdict_keys.pop(next(iter(self.verdict_keys)))

    def open_incidents(self, agent_id: str | None = None) -> list[Incident]:
        return [
            i
            for i in self.incidents.values()
            if i.closed_ms is None and (agent_id is None or i.agent_id == agent_id)
        ]

    def find_open_incident(self, agent_id: str, rule: str) -> Incident | None:
        for inc in reversed(list(self.incidents.values())):
            if inc.agent_id == agent_id and inc.rule == rule and inc.closed_ms is None:
                return inc
        return None

    def add_incident(self, inc: Incident) -> None:
        self.incidents[inc.id] = inc
        if len(self.incidents) > INCIDENTS_MAX:
            # Drop the oldest closed incident first; never drop an open one.
            for iid, old in list(self.incidents.items()):
                if old.closed_ms is not None:
                    del self.incidents[iid]
                    break
            else:
                logger.warning("state: >INCIDENTS_MAX open incidents; keeping all of them")

    def timing(self, source: str) -> collections.deque[float]:
        d = self.timing_samples.get(source)
        if d is None:
            d = self.timing_samples[source] = collections.deque(maxlen=TIMING_SAMPLES_MAX)
        return d

    # ---- persistence ------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "saved_ms": now_ms(),
            "last_ts": self.last_ts,
            "last_hash": self.last_hash,
            "blocked": sorted(self.blocked),
            "modes": self.modes,
            "watermarks": self.watermarks,
            "verdict_keys": self.verdict_keys,
            "incidents": [i.model_dump() for i in self.incidents.values()],
            "recent_alerts": list(self.recent_alerts),
            "recent_events": list(self.recent_events),
            "rings": {a: list(r) for a, r in self.rings.items() if r},
            "hold_enabled": self.hold_enabled,
            "policy": self.policy.model_dump(),
            "heartbeats": self.heartbeats,
            "timing_samples": {k: list(v) for k, v in self.timing_samples.items()},
            "ttd_samples": list(self.ttd_samples),
            "ttc_samples": list(self.ttc_samples),
            "hold_samples": list(self.hold_samples),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), default=str)

    def apply_dict(self, d: dict[str, Any]) -> None:
        self.last_ts = {str(k): int(v) for k, v in d.get("last_ts", {}).items()}
        self.last_hash = {str(k): str(v) for k, v in d.get("last_hash", {}).items()}
        self.blocked = set(d.get("blocked", []))
        self.modes = dict(d.get("modes", {}))
        for a in self.blocked:  # a blocked agent is always quarantined
            self.modes[a] = "quarantined"
        self.watermarks = {str(k): int(v) for k, v in d.get("watermarks", {}).items()}
        self.verdict_keys = {str(k): int(v) for k, v in d.get("verdict_keys", {}).items()}
        self.incidents = {}
        for raw in d.get("incidents", []):
            inc = Incident.model_validate(raw)
            self.incidents[inc.id] = inc
        self.recent_alerts = collections.deque(d.get("recent_alerts", []), maxlen=RECENT_ALERTS_MAX)
        self.recent_events = collections.deque(d.get("recent_events", []), maxlen=RECENT_EVENTS_MAX)
        self.rings = {a: collections.deque(v, maxlen=RING_MAX) for a, v in d.get("rings", {}).items()}
        self.hold_enabled = bool(d.get("hold_enabled", False))
        self.policy = Policy.model_validate(d.get("policy", {}))
        self.heartbeats = dict(d.get("heartbeats", {}))
        self.timing_samples = {
            k: collections.deque(v, maxlen=TIMING_SAMPLES_MAX) for k, v in d.get("timing_samples", {}).items()
        }
        self.ttd_samples = collections.deque(d.get("ttd_samples", []), maxlen=SAMPLES_MAX)
        self.ttc_samples = collections.deque(d.get("ttc_samples", []), maxlen=SAMPLES_MAX)
        self.hold_samples = collections.deque(d.get("hold_samples", []), maxlen=SAMPLES_MAX)

    def load(self) -> bool:
        """Load from self.path. True if a state file was applied."""
        if self.path is None or not self.path.exists():
            return False
        try:
            self.apply_dict(json.loads(self.path.read_text()))
        except Exception as exc:  # noqa: BLE001
            aside = self.path.with_name(f"{self.path.name}.corrupt-{now_ms()}")
            try:
                self.path.rename(aside)
            except OSError:
                aside = self.path
            self.load_error = f"state file unreadable ({type(exc).__name__}); moved to {aside.name}"
            logger.error(f"state: {self.load_error}")
            return False
        logger.info(
            f"state: loaded {self.path} (blocked={sorted(self.blocked)}, incidents={len(self.incidents)})"
        )
        return True

    def save(self) -> None:
        """Atomic write (tmp + rename). Synchronous; small file."""
        if self.path is None:
            return
        text = self.to_json()
        self.dirty = False
        _write_atomic(self.path, text)
        self.last_saved_ms = now_ms()

    async def save_async(self) -> None:
        if self.path is None:
            return
        text = self.to_json()  # snapshot on the loop thread, write off it
        self.dirty = False
        try:
            await asyncio.to_thread(_write_atomic, self.path, text)
            self.last_saved_ms = now_ms()
        except OSError as exc:
            self.dirty = True
            logger.warning(f"state: save failed: {exc!r}")

    async def save_loop(self, interval: float = 1.0) -> None:
        while True:
            await asyncio.sleep(interval)
            if self.dirty:
                await self.save_async()


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
