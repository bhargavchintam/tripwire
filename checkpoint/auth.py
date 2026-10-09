"""Token check for mutating endpoints (master §4): enforced only when PUBLIC=1."""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request

from tripwire.config import Settings, get_settings


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


async def require_token(
    request: Request, x_tripwire_token: str | None = Header(default=None, alias="X-Tripwire-Token")
) -> None:
    s = _settings(request)
    if not s.public:
        return
    if not x_tripwire_token or not hmac.compare_digest(
        x_tripwire_token.encode("utf-8"), s.tripwire_token.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="missing or invalid X-Tripwire-Token")
