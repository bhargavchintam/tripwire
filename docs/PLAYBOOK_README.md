# Tripwire — team playbook

**What this is:** the shared plan for our Cyberdefense Hackathon project (Fri Oct 9 2026). It lets Bindu and Sripadha build Tripwire in two separate Claude Code sessions, independently, and merge without conflicts.

**Tripwire in one line:** your agents have the keys to production — Tripwire is their immune system. It decides on risky actions *before* they run, trips on decoy secrets, traces a poisoned input across the fleet, and cures the fleet with a fix proven by replay.

## How to start (both of you)

1. Open a Claude Code session **in this folder** (or the repo once it exists). `CLAUDE.md` loads automatically.
2. Claude asks: **"Are you Bindu or Sripadha?"** Answer it.
3. Claude reads `00_MASTER_PLAN.md` (shared rules) and your track file, then tells you which checkpoint is current and your open tasks.

## The files

| File | What it's for | Who edits |
|---|---|---|
| `CLAUDE.md` | Auto-loaded router + guardrails; asks who you are | nobody (reference) |
| `00_MASTER_PLAN.md` | **Source of truth:** product, architecture, the frozen contract, git rules, checkpoints, demo | Bindu (frozen after 9:45) |
| `BINDU_platform_and_experience.md` | Bindu's track: checkpoint, ClickHouse, React UI | Bindu |
| `SRIPADHA_agents_and_intelligence.md` | Sripadha's track: agents, detection, AI/models, sponsors | Sripadha |
| `status/bindu.md` | Bindu's progress log (Sripadha reads it) | Bindu only |
| `status/sripadha.md` | Sripadha's progress log (Bindu reads it) | Sripadha only |

## The three rules that keep merges clean

1. **Stay in your lane.** Edit only the directories your track file lists as yours (master §3). The two lists never overlap, so git never sees you both touching one file.
2. **The contract is frozen.** `contracts.py`, `schema.sql`, `.env.example`, `requirements.txt` are Bindu's and frozen at 9:45. Need a change? Don't edit it — post a **Change Request** (format below) and agree in person.
3. **Integrate only at checkpoints** (master §10), each on your own branch into `main`. Before every merge, rebase on `main` and run `make check` (after the 11:15 MVP, also `pytest -m e2e`).

## Change Request format (when you must touch a frozen file or a seam)

Put this in your own `status/<name>.md` and say it out loud to the other person:

```
CONTRACT CHANGE REQUEST · HH:MM
File/seam: <e.g. contracts.py AlertPayload>
Change: <what + why, one or two lines>
Impact on you: <what the other person must update>
```
The owner (Bindu for frozen files) makes the edit after both agree. No silent edits.

## Timeline (details in master §10)

9:30 kickoff (build from doors) · **9:45 contract freeze** · 10:00 first tool call · 10:30 manual block · 11:15 MVP · **12:15 core gate** · 1:30 lunch · 2:30 signature demo · 3:00 Guild go/no-go · **3:30 freeze** · **submit by 4:15** · 5:00 finalist demos.

## Kickoff note

Code is written during the event (a hackathon rule) — the git history is the proof. Before 9:30, only set up accounts, keys and tools (the checklists are in each track file). At kickoff, Bindu runs `git init` here so these playbook files travel with the repo.

_Commit attribution in these files is set to `Claude Opus 5.5`; change it if your team prefers a different line._
