"""Shared run loop + exit-code policy for the simulated bots (deploy_bot, support_bot).

A "session" is one async callable that takes a CheckpointClient and drives a
FakeTools sequence. drive() runs it once (or on a jittered loop) against the
checkpoint and maps the result to a process exit code.

--once (default):
    0  all steps ok
    2  the checkpoint was unreachable, or rejected the request (401 bad token, 422, 5xx)
    3  a step was denied (the session stopped on it)

--loop:
    never exits on its own: a denied step, an unreachable checkpoint or an HTTP error
    is logged and the next session is retried after the interval, so a checkpoint
    restart mid-demo does not kill the bots. Ctrl-C exits 0.

Not a CLI itself; the bots import drive() and pass their own session callable.
"""

from __future__ import annotations

import asyncio
import random
from typing import Awaitable, Callable

from loguru import logger

from agents.checkpoint_client import CheckpointClient, CheckpointDown, CheckpointError
from agents.fake_tools import Denied

SessionFn = Callable[[CheckpointClient], Awaitable[object]]

# Floor for the retry sleep in loop mode, so a dead checkpoint never produces a busy loop.
MIN_RETRY_S = 0.5


def _jitter(interval: float) -> float:
    return max(0.0, interval + random.uniform(-0.3 * interval, 0.3 * interval))


async def _run_once(client: CheckpointClient, session_fn: SessionFn, checkpoint_url: str) -> int:
    """One session -> exit code (0 ok, 2 checkpoint down/error, 3 denied). Never raises for those."""
    try:
        await session_fn(client)
    except Denied as exc:
        logger.warning(f"session stopped: {exc}")
        return 3
    except CheckpointDown as exc:
        logger.error(f"checkpoint unreachable at {checkpoint_url}: {exc}")
        return 2
    except CheckpointError as exc:
        hint = " (check TRIPWIRE_TOKEN / PUBLIC)" if exc.status == 401 else ""
        logger.error(f"checkpoint rejected the request: {exc}{hint}")
        return 2
    return 0


async def drive(
    checkpoint_url: str,
    once: bool,
    loop: bool,
    interval: float,
    session_fn: SessionFn,
) -> int:
    """Run session_fn once or in a loop; return the process exit code (see module doc)."""
    client = CheckpointClient(base_url=checkpoint_url)
    try:
        if not (loop and not once):
            return await _run_once(client, session_fn, checkpoint_url)
        while True:
            code = await _run_once(client, session_fn, checkpoint_url)
            if code == 2:
                logger.info(f"retrying in ~{max(interval, MIN_RETRY_S):.1f}s")
            await asyncio.sleep(max(_jitter(interval), MIN_RETRY_S))
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("interrupted; exiting")
        return 0
    finally:
        await client.aclose()
