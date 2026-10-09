"""Tripwire detection lane (Sripadha): ClickHouse detection SQL + the detector loop.

detection/sql/*.sql   -- the queries (loaded at runtime by detection.sql_loader)
detection/loop.py     -- `uv run python -m detection.loop` : query -> classify -> /block | /alerts -> /heartbeat
detection/metrics.py  -- Timer, RollingSamples (p50/p95), make_heartbeat
"""
