from __future__ import annotations

import subprocess

import httpx
import pytest

from checkpoint.app import create_app
from checkpoint.writer import InMemoryWriter
from tripwire.config import Settings

TOKENS = ["AKIA-TRIPWIRE-DECOY-7Q2", "tw_live_decoy_9f31c0"]


def make_settings(**kw) -> Settings:
    base = {"tripwire_honeytokens": ",".join(TOKENS), "public": False, "hold_check": "stub"}
    base.update(kw)
    return Settings(_env_file=None, **base)


def ch_up() -> bool:
    try:
        r = subprocess.run(
            ["curl", "-sf", "-m", "2", "localhost:8123/ping"], capture_output=True, timeout=5, check=False
        )
        return r.returncode == 0
    except Exception:
        return False


requires_ch = pytest.mark.skipif(not ch_up(), reason="ClickHouse not reachable at localhost:8123")


@pytest.fixture
def writer() -> InMemoryWriter:
    return InMemoryWriter()


@pytest.fixture
def fixtures_dir(tmp_path):
    d = tmp_path / "fixtures"
    d.mkdir()
    return d


@pytest.fixture
def app(tmp_path, writer, fixtures_dir):
    return create_app(
        writer=writer,
        settings=make_settings(),
        state_path=tmp_path / "state.json",
        ch_enabled=False,
        fixtures_dir=fixtures_dir,
        web_dist=None,
    )


@pytest.fixture
def svc(app):
    return app.state.svc


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
