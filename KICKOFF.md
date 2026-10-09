# KICKOFF — first 30 minutes (read at 9:30 AM PT)

This is the exact start sequence. It keeps the "built during the event" rule intact (git history starts at kickoff) and makes the multi-agent build safe — each session's agents are scoped to that person's lane only, so the two tracks can never collide or regress each other.

## Both, at 9:30 sharp
1. In your Claude session **in this folder**, say who you are ("I am Bindu" / "I am Sripadha"). Claude follows `CLAUDE.md` → `00_MASTER_PLAN.md` → your track file.
2. Confirm the clock and that it's the 9:30 kickoff / 9:45 contract-freeze window.

## Bindu — owns 9:30–9:45 (everyone waits on the contract)
Run the contract-freeze block (your track file, Block 1). In order:
```
git init
git add -A && git commit -m "chore: scaffold + frozen contract at kickoff

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
# create GitHub repo, push, invite Sripadha
make setup && make up
```
Then have your session write the frozen files only (contracts.py, schema.sql, readonly_user.sql, .env.example is already drafted — fill real values locally, never commit .env, requirements.txt, docker-compose.yml, Procfile). Commit + push. Post **"CONTRACT FROZEN, pushed"** in `status/bindu.md`.

## Sripadha — until the freeze
Set up keys (AkashML `/v1/models`, pick the two models), then **clone the moment Bindu posts the freeze**. Build only against `contracts.py` + `schema.sql`.

## Spawning the build agents (each session, AFTER the freeze)
Each of you runs your own fan-out. **An agent may only touch directories your track file lists as yours** — put that sentence in every agent prompt. This is what prevents regressions across the two tracks.

- **Bindu**, parallel agents (one lane each, all inside your directories):
  1. `checkpoint/` — app, state, writer, bus (endpoints per master §4)
  2. `web/` — React console against `tripwire/mock_server.py` until 11:15
  3. `data/` + `tests/integration/test_schema.py` — schema load, seed, background load
  Keep `contracts.py`/`schema.sql` edits to yourself (main session), never a sub-agent, so the seam stays stable.

- **Sripadha**, parallel agents (one lane each):
  1. `agents/` — bots, fake tools, replay, honeytokens
  2. `detection/` (+ `detection/sql/`) — funnel, loop, metrics
  3. `ai/` — llm, quick_check (the `classify()` seam — keep its signature per master §6), fake_llm
  Later lanes (CP5+): `detection/outbreak.py`, `ai/quorum.py`, `ai/investigator.py`+`sqlguard.py`, `eval/`, `guild/`, `semgrep/`.

Rules for every agent prompt (paste these):
- "Only create/edit files under <your lane>. Never touch `contracts.py`, `schema.sql`, or the other person's directories."
- "Import types from `tripwire/contracts.py`; if you need a change there, stop and report it as a CONTRACT CHANGE REQUEST."
- "Match the endpoint/schema names in `00_MASTER_PLAN.md` exactly."

## Merge discipline (both, all day)
- Work on `bindu/<topic>` or `sripadha/<topic>`.
- Before merging to `main`: `git pull --rebase origin main` → `make check` (after 11:15 also `make e2e`) → merge only when green.
- Integrate at the checkpoints in master §10; post status at each.

## The gates (don't skip)
9:45 contract freeze · 10:00 first tool call · 10:30 manual block → denied · **11:15 MVP** · **12:15 core gate ×3** · 1:30 lunch · 2:30 signature · 3:00 Guild go/no-go · **3:30 freeze** · **submit by 4:15**.
If the core isn't green at 12:15, both of you drop everything else and fix the core.
