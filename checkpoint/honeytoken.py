"""Honeytoken scanner (master §1 "Trips", §7a).

scan(payload, tokens) is True when a decoy value appears in the payload either
raw, or inside any base64 / base64url run (>= 12 chars) that decodes to text
containing it. One level of nested encoding is also checked. Pure function:
never logs or returns the payload.
"""

from __future__ import annotations

import base64
import binascii
import re

_B64_RUN = re.compile(r"[A-Za-z0-9+/_-]{12,}={0,2}")
_URLSAFE_TO_STD = str.maketrans("-_", "+/")
_MAX_DEPTH = 2


def _decode(run: str) -> bytes | None:
    s = run.rstrip("=").translate(_URLSAFE_TO_STD)
    if len(s) % 4 == 1:  # cannot be valid base64; drop the dangling char
        s = s[:-1]
    if len(s) < 8:
        return None
    s += "=" * (-len(s) % 4)
    try:
        return base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        return None


def _contains(text: str, tokens: list[str]) -> bool:
    return any(t in text for t in tokens)


def _scan(text: str, tokens: list[str], depth: int) -> bool:
    if _contains(text, tokens):
        return True
    if depth >= _MAX_DEPTH:
        return False
    for m in _B64_RUN.finditer(text):
        run = m.group(0)
        # Try a few alignments in case non-base64 word chars are glued to the front.
        for off in range(4):
            if len(run) - off < 12:
                break
            raw = _decode(run[off:])
            if raw is None:
                continue
            if _scan(raw.decode("latin-1"), tokens, depth + 1):
                return True
    return False


def scan(payload: str, tokens: list[str]) -> bool:
    toks = [t for t in tokens if t]
    if not payload or not toks:
        return False
    return _scan(payload, toks, 0)
