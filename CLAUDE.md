# Tripwire — session router (read this first, every session)

This folder is the shared playbook for the **Tripwire** hackathon project (Cyberdefense Hackathon, Oct 9 2026). Two people build it in two separate Claude Code sessions:

- **Bindu** — Platform & Experience (checkpoint backend, ClickHouse, React UI)
- **Sripadha** — Agents & Intelligence (agents, detection, AI/models, sponsors glue)

## What you (Claude) must do at the start of every session

1. Ask the user **one question first: "Are you Bindu or Sripadha?"** Do not start work until they answer.
2. Then read, in order:
   - `00_MASTER_PLAN.md` — the shared source of truth (architecture, the frozen contract, git rules, checkpoints). **Never contradict it.**
   - If **Bindu**: read and follow `BINDU_platform_and_experience.md`.
   - If **Sripadha**: read and follow `SRIPADHA_agents_and_intelligence.md`.
3. Before writing code, confirm which clock time it is and which checkpoint (§Timeline in the master plan) is current.

## Hard rules for both sessions (do not break these)

- **Stay in your lane.** Only create/edit files in the directories your track file lists as *yours*. Never edit the other person's directories. This is what lets both sessions merge without conflicts.
- **The contract is frozen.** `tripwire/contracts.py`, `data/schema.sql`, `.env.example`, and `requirements.txt` are owned by Bindu and frozen after the 9:45 contract checkpoint. If you need a change, do NOT edit them unilaterally — write a `CONTRACT CHANGE REQUEST` in your status file (`status/<yourname>.md`) and tell the human to confirm with the other person in person.
- **Code is written during the event.** It is a hackathon rule. Before the 9:30 AM kickoff (build from doors), only set up accounts, keys and tools — write no project code. The git history is the proof.
- **Honesty.** Every number shown to judges must be measured, never invented. Tools are record-only; no real secret, shell, or network side effect. Label synthetic data. Show `decision_source` truthfully. Never claim a capability that isn't demonstrated.
- **Communicate through status files.** Append progress and blockers to your own `status/<yourname>.md`; read the other person's before integrating. Never edit the other person's status file.
- **Git:** work on your own branch, integrate to `main` only at the checkpoints in the master plan. Commit at every gate. End commit messages with:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

## If the user asks for something that would change the architecture

Pause and say it would diverge from `00_MASTER_PLAN.md`. Small tweaks are fine; major architecture or contract changes need both teammates to agree. Suggest they note it as a `CONTRACT CHANGE REQUEST` and sync in person.
