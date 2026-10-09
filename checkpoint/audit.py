"""Per-agent tamper-evident hash chain (master §5 prev_hash/hash)."""

from __future__ import annotations

import hashlib
from typing import Any, Iterable


def chain_hash(
    prev_hash: str, ts_ms: int, agent_id: str, action: str, target: str, result: str, reason: str
) -> str:
    msg = f"{prev_hash}|{int(ts_ms)}|{agent_id}|{action}|{target}|{result}|{reason}"
    return hashlib.sha256(msg.encode("utf-8")).hexdigest()


def verify_chain(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Rows ordered by ts. The first row anchors the chain (its prev_hash is taken as
    given); every later row must link to the previous row's hash, and every row's
    hash must recompute. Returns {events, intact, first_break_ts_ms}."""
    n = 0
    prev: str | None = None
    first_break: int | None = None
    for r in rows:
        n += 1
        ts = int(r["ts_ms"])
        expected = chain_hash(
            str(r.get("prev_hash", "")),
            ts,
            str(r["agent_id"]),
            str(r["action"]),
            str(r["target"]),
            str(r["result"]),
            str(r.get("reason", "")),
        )
        linked = prev is None or r.get("prev_hash", "") == prev
        if first_break is None and (not linked or r.get("hash", "") != expected):
            first_break = ts
        prev = str(r.get("hash", ""))
    return {"events": n, "intact": first_break is None, "first_break_ts_ms": first_break}
