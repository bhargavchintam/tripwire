"""Provider wrapper over openai.AsyncOpenAI: AkashML for verdicts, OpenAI only as the price-comparison model.

    from ai.llm import akashml, resolve_models
    llm = akashml()                              # None when AKASHML_API_KEY is empty
    small, large = await resolve_models(llm)     # AKASHML_MODEL_SMALL/LARGE, else a heuristic over /v1/models
    r = await llm.chat_json(messages, model=small, timeout_s=2.5)
    r.obj                                        # dict parsed from the reply, or None; r.error on failure

No network at import time; clients are built on first use. chat_json() never raises: transport,
HTTP (5xx included) and timeout errors land in ChatJSON.error and are never retried. Tests inject
an AsyncOpenAI bound to tests/fakes/fake_llm.py through httpx.ASGITransport.

Documented caches: _INSTANCES (one LLM per provider/base_url/key fingerprint, rebuilt when the event
loop it was first used on is gone, so ``asyncio.run()`` per case keeps working) and, per LLM, the
/v1/models id list (fetched once; a failure is remembered for MODELS_RETRY_AFTER_S).

CLI (reads AKASHML_API_KEY from .env; never prints the key):
    uv run python -m ai.llm models                 # list ids + the heuristic small/large pick
    uv run python -m ai.llm ping [--model ID]      # one tiny JSON chat: latency and tokens
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Literal

from loguru import logger
from openai import APIStatusError, AsyncOpenAI

from tripwire.config import get_settings

Provider = Literal["akashml", "openai"]

DEFAULT_TIMEOUT_S = 10.0
MODELS_TIMEOUT_S = 5.0
MODELS_RETRY_AFTER_S = 30.0
OPENAI_BASE_URL = "https://api.openai.com/v1"
MAX_TOKENS = 400  # verdict JSON is ~50 tokens; gpt-oss-style models also count their reasoning here

# Model-pick heuristic (used only when AKASHML_MODEL_SMALL/LARGE are not set). Measured on AkashML's
# list on 2026-10-09: Qwen3.* and GLM default to a thinking preamble and return EMPTY content once
# max_tokens is spent, so they are ranked last; instruct/chat variants answer plain JSON fastest.
#   small: non-thinking, instruct/chat/mini first, then the smallest parameter count
#   large: non-thinking, instruct/chat first, then the largest parameter count, never the small pick
# Parameter count = the first "<n>B" token not glued to a letter ("A3B"/"A22B" active-expert suffixes
# and "FP8" do not count).
THINKING_RE = re.compile(r"qwen3|glm|deepseek-r|(^|[^a-z0-9])r1([^a-z0-9]|$)|think|reason", re.I)
CHATTY_RE = re.compile(r"instruct|chat", re.I)
MINI_RE = re.compile(r"mini|small", re.I)
SIZE_RE = re.compile(r"(?<![A-Za-z0-9])(\d{1,3}(?:\.\d)?)b(?![A-Za-z0-9])", re.I)


@dataclass
class ChatJSON:
    obj: dict[str, Any] | None
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    error: str | None = None


def extract_json(text: str) -> dict[str, Any] | None:
    """First JSON object in text; tolerates ``` fences and prose before/after it. None if there is none."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[A-Za-z0-9_-]*[ \t]*\r?\n?", "", s)
        s = re.sub(r"\r?\n?```\s*$", "", s)
    dec = json.JSONDecoder()
    idx = s.find("{")
    tries = 0
    while idx != -1 and tries < 20:
        tries += 1
        try:
            obj, _ = dec.raw_decode(s, idx)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            return obj
        idx = s.find("{", idx + 1)
    return None


def short_error(exc: BaseException, timeout_s: float) -> str:
    if isinstance(exc, asyncio.TimeoutError):  # TimeoutError on 3.11+
        return f"timeout after {timeout_s:g} s"
    if isinstance(exc, APIStatusError):
        return f"{type(exc).__name__} {exc.status_code}"
    msg = " ".join(str(exc).split())[:120]
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__


def model_size_b(model_id: str) -> float | None:
    """Parameter count in billions parsed from the id ("Llama-3.3-70B-Instruct" -> 70.0), None if absent."""
    m = SIZE_RE.search(model_id)
    return float(m.group(1)) if m else None


def pick_models(ids: list[str]) -> tuple[str, str]:
    """(small, large) from a model id list by the heuristic documented at THINKING_RE/SIZE_RE."""
    if not ids:
        return "", ""

    def small_key(m: str) -> tuple[bool, bool, float]:
        size = model_size_b(m)
        return (bool(THINKING_RE.search(m)), not (CHATTY_RE.search(m) or MINI_RE.search(m)), size or 999.0)

    def large_key(m: str) -> tuple[bool, bool, float]:
        size = model_size_b(m)
        return (bool(THINKING_RE.search(m)), not CHATTY_RE.search(m), -(size or -1.0))

    small = min(ids, key=small_key)
    larges = sorted(ids, key=large_key)
    large = next((m for m in larges if m != small), larges[0])
    return small, large


THINKING_MODEL_RE = re.compile(r"(?i)qwen3|glm-")


def provider_extra_body(model: str) -> dict[str, Any] | None:
    """Provider switches a model needs to answer plainly. Qwen3/GLM families on AkashML return EMPTY content
    unless thinking is disabled (measured 2026-10-09: Qwen3.6-35B-A3B 0.9-1.3 s with it, timeouts without),
    so production callers pass this through chat_json(extra_body=...). None for every other model."""
    if THINKING_MODEL_RE.search(model or ""):
        return {"chat_template_kwargs": {"enable_thinking": False}}
    return None


class LLM:
    """One OpenAI-compatible endpoint. Pass ``client`` to inject a pre-built AsyncOpenAI (tests)."""

    def __init__(self, provider: Provider, api_key: str, base_url: str, client: AsyncOpenAI | None = None):
        self.provider: Provider = provider
        self.base_url = base_url
        if client is None:
            if not api_key:
                raise ValueError(f"{provider}: api_key is empty")
            client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0, timeout=DEFAULT_TIMEOUT_S)
        self.client = client
        self._models: list[str] | None = None  # cache: /v1/models ids, filled once by cached_models()
        self._models_failed_at: float = 0.0
        self._models_error: str = ""

    @property
    def models_cached(self) -> list[str] | None:
        return self._models

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        model: str,
        timeout_s: float,
        max_tokens: int = MAX_TOKENS,
        temperature: float = 0.0,
        extra_body: dict[str, Any] | None = None,
    ) -> ChatJSON:
        """One chat completion, parsed as JSON. Never raises; see ChatJSON.error.

        extra_body (optional, default None = not sent) is passed through to the provider unchanged, e.g.
        {"chat_template_kwargs": {"enable_thinking": False}} so Qwen3/GLM answer without a thinking preamble
        (used by eval/model_compare.py; production callers do not pass it)."""
        t0 = time.perf_counter()
        extra: dict[str, Any] = {"extra_body": extra_body} if extra_body else {}
        try:
            resp = await asyncio.wait_for(
                self.client.with_options(max_retries=0, timeout=timeout_s).chat.completions.create(
                    model=model,
                    messages=messages,  # type: ignore[arg-type]
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **extra,
                ),
                timeout=timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 - the caller falls back to the rule, never crashes
            ms = round((time.perf_counter() - t0) * 1000, 3)
            return ChatJSON(obj=None, text="", latency_ms=ms, error=short_error(exc, timeout_s))
        ms = round((time.perf_counter() - t0) * 1000, 3)
        text = ""
        if getattr(resp, "choices", None):
            text = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
        tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)
        return ChatJSON(obj=extract_json(text), text=text, tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=ms)

    async def list_models(self, timeout_s: float = MODELS_TIMEOUT_S) -> list[str]:
        """Ids from GET /v1/models. Raises on failure (resolve_models handles that)."""
        page = await asyncio.wait_for(
            self.client.with_options(max_retries=0, timeout=timeout_s).models.list(), timeout=timeout_s
        )
        return [m.id for m in page.data]

    async def cached_models(self, timeout_s: float = MODELS_TIMEOUT_S) -> list[str]:
        """list_models() once per instance; a failure is remembered for MODELS_RETRY_AFTER_S."""
        if self._models is not None:
            return self._models
        if time.monotonic() - self._models_failed_at < MODELS_RETRY_AFTER_S:
            raise RuntimeError(self._models_error)
        try:
            self._models = await self.list_models(timeout_s=timeout_s)
        except Exception as exc:
            self._models_failed_at = time.monotonic()
            self._models_error = short_error(exc, timeout_s)
            logger.warning(
                f"llm: {self.provider} /v1/models failed ({self._models_error}); "
                "set AKASHML_MODEL_SMALL/LARGE to skip discovery"
            )
            raise
        logger.info(f"llm: {self.provider} lists {len(self._models)} models")
        return self._models


# cache: (provider, base_url, sha256(key)[:12]) -> (event loop the client was first used on, LLM).
# A settings change creates a new client; so does a closed/different loop, because the pooled httpx
# connections inside AsyncOpenAI belong to the loop that opened them ("Event loop is closed" otherwise).
_INSTANCES: dict[tuple[str, str, str], tuple[asyncio.AbstractEventLoop | None, LLM]] = {}


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _instance(provider: Provider, api_key: str, base_url: str) -> LLM:
    key = (provider, base_url, hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12])
    loop = _running_loop()
    hit = _INSTANCES.get(key)
    if hit is not None:
        bound, inst = hit
        if bound is None or bound is loop or (loop is None and not bound.is_closed()):
            if bound is None and loop is not None:
                _INSTANCES[key] = (loop, inst)
            return inst
        logger.debug(f"llm: rebuilding {provider} client for a new event loop")
    inst = LLM(provider, api_key, base_url)
    _INSTANCES[key] = (loop, inst)
    return inst


def akashml() -> LLM | None:
    """AkashML client from settings, or None when AKASHML_API_KEY is empty."""
    s = get_settings()
    if not s.akashml_api_key.strip():
        return None
    return _instance("akashml", s.akashml_api_key.strip(), s.akashml_base_url.strip())


def openai_llm() -> LLM | None:
    """OpenAI client from settings (price comparison only), or None when OPENAI_API_KEY is empty."""
    s = get_settings()
    if not s.openai_api_key.strip():
        return None
    return _instance("openai", s.openai_api_key.strip(), OPENAI_BASE_URL)


async def resolve_models(llm: LLM, timeout_s: float = MODELS_TIMEOUT_S) -> tuple[str, str]:
    """(small, large) model ids: configured names when set, else pick_models() over the cached list.

    Discovery (only when needed) is bounded by timeout_s and never raises. Returns "" for an id that
    is neither configured nor discoverable; callers fall back to rules.
    """
    s = get_settings()
    if llm.provider == "openai":
        small = large = s.openai_model.strip()
    else:
        small, large = s.akashml_model_small.strip(), s.akashml_model_large.strip()
    if small and large:
        return small, large
    try:
        ids = await llm.cached_models(timeout_s=timeout_s)
    except Exception as exc:  # noqa: BLE001 - already logged by cached_models
        logger.debug(f"llm: model discovery unavailable ({exc})")
        return small, large
    hs, hl = pick_models(ids)
    return small or hs, large or hl


async def resolve_model(llm: LLM, which: Literal["small", "large"], timeout_s: float = MODELS_TIMEOUT_S) -> str:
    """One model id. The configured name wins without any network call; else discovery via resolve_models()."""
    s = get_settings()
    if llm.provider == "openai":
        configured = s.openai_model.strip()
    else:
        configured = (s.akashml_model_small if which == "small" else s.akashml_model_large).strip()
    if configured:
        return configured
    small, large = await resolve_models(llm, timeout_s=timeout_s)
    return small if which == "small" else large


def _cli() -> None:
    import typer

    app = typer.Typer(add_completion=False, help="AkashML/OpenAI client checks (no key is ever printed).")

    def _llm(provider: str) -> LLM:
        llm = akashml() if provider == "akashml" else openai_llm()
        if llm is None:
            typer.echo(f"{provider}: no API key in .env")
            raise typer.Exit(1)
        return llm

    @app.command()
    def models(provider: str = "akashml") -> None:
        """List model ids and show the heuristic small/large pick next to the configured ones."""
        llm = _llm(provider)
        try:
            ids = asyncio.run(llm.list_models())
        except Exception as exc:  # noqa: BLE001 - CLI: report and exit non-zero
            typer.echo(f"{provider}: GET /v1/models failed: {short_error(exc, MODELS_TIMEOUT_S)}")
            raise typer.Exit(1) from None
        for m in ids:
            typer.echo(m)
        small, large = pick_models(ids)
        s = get_settings()
        typer.echo(f"\nheuristic pick: small={small!r} large={large!r}")
        typer.echo(f"configured:     small={s.akashml_model_small!r} large={s.akashml_model_large!r}")

    @app.command()
    def ping(model: str = "", provider: str = "akashml", timeout_s: float = 10.0) -> None:
        """One tiny JSON chat; prints latency, tokens and the parsed object."""
        llm = _llm(provider)
        msgs = [{"role": "user", "content": 'Reply with only this JSON object: {"ok": true}'}]

        async def run() -> tuple[str, ChatJSON]:
            m = model or (await resolve_models(llm))[0]
            return m, await llm.chat_json(msgs, model=m, timeout_s=timeout_s)

        model, r = asyncio.run(run())  # one loop: resolve + chat share the client's connections
        out = {
            "provider": provider,
            "model": model,
            "obj": r.obj,
            "error": r.error,
            "latency_ms": r.latency_ms,
            "tokens_in": r.tokens_in,
            "tokens_out": r.tokens_out,
            "text": r.text[:200],
        }
        typer.echo(json.dumps(out, indent=2))
        if r.error or not isinstance(r.obj, dict):
            raise typer.Exit(1)

    app()


if __name__ == "__main__":
    _cli()
