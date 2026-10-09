"""Tripwire agents lane (Sripadha, master §3).

Record-only simulated agents that drive the checkpoint over HTTP only:

    deploy_bot / support_bot  -- normal action loops (POST /tool)
    replay                    -- replay a fixtures/*.json Scenario, local or via /demo/replay
    fake_tools                -- the record-only tool surface (no real IO, ever)
    checkpoint_client         -- async httpx client for the checkpoint HTTP API
    honeytokens               -- the synthetic /app/.env, decoys and fake inputs
    stub_checkpoint           -- a tiny stand-in checkpoint for working offline

Nothing in this package performs a real file read, subprocess, socket or outbound
network call. The only network any agent makes is to the Tripwire checkpoint.
Run everything from the repo root with `uv run python -m agents.<module>`.
"""
