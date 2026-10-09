"""Per-token prices behind the eval runner's "cost per 1,000 events" column (AkashML vs OpenAI).

AkashML: read LIVE from GET {AKASHML_BASE_URL}/models. Every model listed there carries
``pricing.input`` / ``pricing.output`` in USD per token (decimal strings). The key from .env is sent
as a bearer token and is never logged or printed. The model priced is AKASHML_MODEL_SMALL (the
quick-check model) or, when it is unset, ai.llm.pick_models over the listed ids.

OpenAI (price-comparison column only; no OpenAI call is ever made): the published price of
gpt-4o-mini, the closest small model. https://openai.com/api/pricing/ is tried first; it answers
403 to plain HTTP clients (measured 2026-10-09), so https://platform.openai.com/docs/pricing is
tried next: its server-rendered table embeds ``["gpt-4o-mini"],[0,<input>],[0,<cached>],[0,<output>]``
in USD per 1M tokens (the standard tier comes first; the batch and priority tables repeat the model
later). If neither page can be fetched and parsed, OPENAI_CONSTANT_PER_1M (dated) is used and the
price source says "constant (fetch failed)".

    from eval.pricing import fetch_prices, cost_usd, cost_per_1000_events
    prices = await fetch_prices()                 # both lookups, each bounded by timeout_s
    usd = cost_usd(812, 31, prices.akashml)       # one quick check: tokens_in, tokens_out

CLI (JSON with both price points and their sources; the key is never printed):
    uv run python -m eval.pricing
    uv run python -m eval.pricing --model meta-llama/Llama-3.3-70B-Instruct --timeout-s 5
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Annotated, Any, Optional

import httpx
import typer
from loguru import logger

from ai.llm import pick_models
from tripwire.config import get_settings

OPENAI_COMPARISON_MODEL = "gpt-4o-mini"
OPENAI_PRICING_URLS: tuple[str, ...] = (
    "https://openai.com/api/pricing/",
    "https://platform.openai.com/docs/pricing",
)
# gpt-4o-mini, standard tier, USD per 1M tokens (input, output): OpenAI's long-standing list price for the
# model. Used only when both fetches fail; the price source then says so and names the day the fetch was
# attempted (the pages answer 403 to non-browser clients, measured 2026-10-09), never that it was re-verified.
OPENAI_CONSTANT_PER_1M: tuple[float, float] = (0.15, 0.60)
OPENAI_CONSTANT_DATE = "2026-10-09"  # day the live fetch was last attempted (and failed)
FETCH_TIMEOUT_S = 8.0
MAX_BODY_BYTES = 4_000_000
USER_AGENT = "tripwire-eval/0.1 (hackathon price lookup)"

_Q = r'(?:&quot;|")'
_NUM = r"(\d+(?:\.\d+)?)"
_SKIP_CELL = r"\[0,(?:\d+(?:\.\d+)?|null|" + _Q + r"[^\]]*?" + _Q + r")\]"  # the cached-input cell


@dataclass(frozen=True)
class PricePoint:
    provider: str  # "akashml" | "openai"
    model: str
    input_per_token: float  # USD
    output_per_token: float  # USD
    source: str  # where the numbers came from (URL + live / constant)
    fetched: bool  # True when read live during this run

    @property
    def per_1m(self) -> tuple[float, float]:
        return (self.input_per_token * 1e6, self.output_per_token * 1e6)


@dataclass(frozen=True)
class Prices:
    akashml: Optional[PricePoint]
    openai: PricePoint
    priced_on: str  # ISO date, UTC

    def label(self) -> str:
        """Short 'priced_on' string for /evidence: the date plus how each price was obtained."""
        ak = "AkashML /v1/models live" if self.akashml and self.akashml.fetched else "AkashML price not fetched"
        oa = "OpenAI page live" if self.openai.fetched else "OpenAI constant (fetch failed)"
        return f"{self.priced_on} ({ak}; {oa})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "priced_on": self.priced_on,
            "priced_on_label": self.label(),
            "akashml": asdict(self.akashml) if self.akashml else None,
            "openai": asdict(self.openai),
        }


# ---------------------------------------------------------------------------- arithmetic
def cost_usd(tokens_in: int, tokens_out: int, price: PricePoint) -> float:
    """USD for one call: tokens_in * input price + tokens_out * output price (prices per token)."""
    return max(0, int(tokens_in)) * price.input_per_token + max(0, int(tokens_out)) * price.output_per_token


def mean_cost_per_call(calls: list[tuple[int, int]], price: PricePoint) -> Optional[float]:
    """Mean of cost_usd over (tokens_in, tokens_out) pairs; None when there are no calls."""
    if not calls:
        return None
    return sum(cost_usd(i, o, price) for i, o in calls) / len(calls)


def cost_per_1000_events(n_calls: int, n_events: int, mean_cost: Optional[float]) -> Optional[float]:
    """(classify calls / events replayed) * 1000 * mean cost per call. None when nothing was measured."""
    if n_calls <= 0 or n_events <= 0 or mean_cost is None:
        return None
    return (n_calls / n_events) * 1000.0 * mean_cost


# ---------------------------------------------------------------------------- parsing
def _float(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f >= 0 else None


def parse_akashml_models(payload: Any, model: Optional[str], source: str) -> Optional[PricePoint]:
    """PricePoint for `model` (or the heuristic small pick) from a GET /v1/models body."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return None
    entries = [m for m in data if isinstance(m, dict) and m.get("id")]
    ids = [str(m["id"]) for m in entries]
    chosen = (model or "").strip() or (pick_models(ids)[0] if ids else "")
    entry = next((m for m in entries if str(m["id"]) == chosen), None)
    if entry is None:
        return None
    pricing = entry.get("pricing") if isinstance(entry.get("pricing"), dict) else {}
    inp, out = _float(pricing.get("input")), _float(pricing.get("output"))
    if inp is None or out is None:
        return None
    return PricePoint("akashml", chosen, inp, out, source=source, fetched=True)


def parse_openai_pricing_html(html: str, model: str = OPENAI_COMPARISON_MODEL) -> Optional[tuple[float, float]]:
    """(input, output) in USD per 1M tokens for `model` from the docs pricing page.

    The page serializes each table row as ``[0,"<model>"],[0,<input>],[0,<cached>],[0,<output>]``
    (quotes may be HTML-escaped). The first row for the model is the standard tier (the batch and
    priority tables come later on the page). ``gpt-4o-mini-tts`` and other suffixed ids do not match
    because the closing quote must follow the model id directly."""
    pat = re.compile(re.escape(model) + _Q + r"\],\[0," + _NUM + r"\]," + _SKIP_CELL + r",\[0," + _NUM + r"\]")
    m = pat.search(html or "")
    if m is None:
        return None
    inp, out = float(m.group(1)), float(m.group(2))
    if not (0.0 < inp < 1000.0 and 0.0 < out < 1000.0):
        return None
    return inp, out


def constant_openai_price(model: str = OPENAI_COMPARISON_MODEL, why: str = "") -> PricePoint:
    inp, out = OPENAI_CONSTANT_PER_1M
    src = (
        f"constant (fetch failed): ${inp:g} in / ${out:g} out per 1M tokens, {model}'s list price as published at "
        f"{OPENAI_PRICING_URLS[0]}; the page could not be fetched on {OPENAI_CONSTANT_DATE}, so the value was "
        "not re-verified live"
    )
    if why:
        src += f" [{why}]"
    return PricePoint("openai", model, inp / 1e6, out / 1e6, source=src, fetched=False)


# ---------------------------------------------------------------------------- fetching
def _short(exc: BaseException) -> str:
    msg = " ".join(str(exc).split())[:120]
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__


async def _get(
    url: str, *, headers: dict[str, str] | None, timeout_s: float, client: httpx.AsyncClient | None
) -> httpx.Response:
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    if client is not None:
        r = await client.get(url, headers=hdrs, timeout=timeout_s)
    else:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as c:
            r = await c.get(url, headers=hdrs)
    r.raise_for_status()
    if len(r.content) > MAX_BODY_BYTES:
        raise ValueError(f"body too large ({len(r.content)} bytes)")
    return r


async def fetch_akashml_price(
    model: Optional[str] = None,
    *,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout_s: float = FETCH_TIMEOUT_S,
    client: httpx.AsyncClient | None = None,
) -> Optional[PricePoint]:
    """Live AkashML price for `model` (default AKASHML_MODEL_SMALL). None when the key is empty,
    the request fails or the model is not listed; the reason is logged, never the key."""
    s = get_settings()
    base = (base_url if base_url is not None else s.akashml_base_url).strip().rstrip("/")
    key = (api_key if api_key is not None else s.akashml_api_key).strip()
    chosen = (model if model is not None else s.akashml_model_small).strip() or None
    if not key:
        logger.warning("pricing: AKASHML_API_KEY is empty; AkashML price not fetched")
        return None
    url = f"{base}/models"
    try:
        r = await _get(url, headers={"Authorization": f"Bearer {key}"}, timeout_s=timeout_s, client=client)
        payload = r.json()
    except Exception as exc:  # noqa: BLE001 - a price lookup must never break the eval
        logger.warning(f"pricing: GET {url} failed ({_short(exc)})")
        return None
    pp = parse_akashml_models(payload, chosen, source=f"{url} (live)")
    if pp is None:
        logger.warning(f"pricing: no pricing for {chosen or '(heuristic small pick)'} in GET {url}")
    return pp


async def fetch_openai_price(
    *,
    model: str = OPENAI_COMPARISON_MODEL,
    urls: tuple[str, ...] = OPENAI_PRICING_URLS,
    timeout_s: float = FETCH_TIMEOUT_S,
    client: httpx.AsyncClient | None = None,
) -> PricePoint:
    """Published OpenAI price for `model` from the first page that can be fetched and parsed,
    else the dated constant (source marked "constant (fetch failed)")."""
    errors: list[str] = []
    for url in urls:
        try:
            html = (await _get(url, headers=None, timeout_s=timeout_s, client=client)).text
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url}: {_short(exc)}")
            continue
        parsed = parse_openai_pricing_html(html, model)
        if parsed is None:
            errors.append(f"{url}: no price row for {model}")
            continue
        return PricePoint(
            "openai", model, parsed[0] / 1e6, parsed[1] / 1e6, source=f"{url} (live, standard tier)", fetched=True
        )
    logger.warning(f"pricing: OpenAI price not fetched ({'; '.join(errors)}); using the dated constant")
    return constant_openai_price(model, why="; ".join(errors))


async def fetch_prices(
    *,
    akashml_model: Optional[str] = None,
    timeout_s: float = FETCH_TIMEOUT_S,
    client: httpx.AsyncClient | None = None,
) -> Prices:
    """Both price points, fetched concurrently. Never raises."""
    ak, oa = await asyncio.gather(
        fetch_akashml_price(akashml_model, timeout_s=timeout_s, client=client),
        fetch_openai_price(timeout_s=timeout_s, client=client),
    )
    return Prices(akashml=ak, openai=oa, priced_on=datetime.now(timezone.utc).date().isoformat())


# ---------------------------------------------------------------------------- CLI
app = typer.Typer(add_completion=False, help="Print the per-token prices the eval runner uses (JSON).")


@app.command()
def main(
    model: Annotated[str, typer.Option("--model", help="AkashML model id (default AKASHML_MODEL_SMALL)")] = "",
    timeout_s: Annotated[float, typer.Option("--timeout-s", help="per-request timeout")] = FETCH_TIMEOUT_S,
) -> None:
    prices = asyncio.run(fetch_prices(akashml_model=model or None, timeout_s=timeout_s))
    typer.echo(json.dumps(prices.to_dict(), indent=2))


if __name__ == "__main__":
    app()
