# tripwire-deploy-bot (Guild Native agent)

Published to `bindubhargavareddy~tripwire-deploy-bot` and installed in workspace `bindubhargavareddy~tripwire`.
Its only tool is the `tripwire` integration (`POST /tool` through a cloudflared tunnel to
`tripwire/guild_proxy.py`, which forces the `guild:` agent id and forwards to the checkpoint).
Every action it takes is recorded in ClickHouse and decided by Tripwire before it runs; tools are record-only.
Trigger it with `POST /guild/run` on the checkpoint (or the Sponsors tab button). Setup: `guild/RUNBOOK.md`.
