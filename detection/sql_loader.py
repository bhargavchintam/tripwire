"""Load detection SQL from detection/sql/<name>.sql and build the watermark parameters.

    from detection.sql_loader import load_sql, watermark_params
    sql = load_sql("funnel")                       # cached text of detection/sql/funnel.sql
    params = {**watermark_params(status.watermarks), "window_s": 300}

Every query takes the per-agent watermarks as two parallel arrays ({ids:Array(String)}, {wms:Array(Int64)})
because ClickHouse's transform() rejects empty arrays: watermark_params() substitutes the sentinel pair
(['__none__'], [0]) when no agent has a watermark yet.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

SQL_DIR = Path(__file__).resolve().parent / "sql"
SENTINEL_AGENT = "__none__"
_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def sql_path(name: str) -> Path:
    if not _NAME_RE.match(name or ""):
        raise ValueError(f"bad sql name {name!r} (expected [A-Za-z0-9_]+)")
    return SQL_DIR / f"{name}.sql"


@lru_cache(maxsize=None)
def load_sql(name: str) -> str:
    """Text of detection/sql/<name>.sql (cached for the life of the process)."""
    return sql_path(name).read_text(encoding="utf-8")


def available_sql() -> list[str]:
    return sorted(p.stem for p in SQL_DIR.glob("*.sql"))


def watermark_params(watermarks: dict[str, int] | None) -> dict[str, list]:
    """{"ids": [...], "wms": [...]} for the transform() watermark filter; sentinel pair when empty."""
    items = sorted((str(k), int(v)) for k, v in (watermarks or {}).items() if k)
    if not items:
        return {"ids": [SENTINEL_AGENT], "wms": [0]}
    return {"ids": [k for k, _ in items], "wms": [v for _, v in items]}
