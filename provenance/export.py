"""Export Bindu's Claude Code session transcripts to scrubbed Markdown (AI-written-code provenance).

    uv run python provenance/export.py OUT.md SESSION.jsonl [SESSION.jsonl ...]

Keeps the conversation (user prompts, assistant text, tool calls and short tool results) in time
order and drops harness noise. Every secret is scrubbed BEFORE writing: all values of KEY / TOKEN /
PASSWORD / SECRET / HOST settings in .env and .env.cloud (and each half of id:secret pairs), the
Guild proxy token, the tunnel URL, e-mail addresses and generic `key=value` secrets. The script then
re-reads the output and refuses (exit 1, file deleted) if any known secret is still present.
Prints only counts, never a secret.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRET_KEY_RE = re.compile(r"(KEY|TOKEN|PASSWORD|SECRET|HOST|TRIGGER_URL)", re.I)
TOOL_INPUT_MAX = 1500
TOOL_RESULT_MAX = 600
TEXT_MAX = 6000

GENERIC = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[email]"),
    (re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com"), "https://[tunnel]"),
    (re.compile(r"[a-z0-9.-]+\.clickhouse\.cloud"), "[clickhouse-cloud-host]"),
    (re.compile(r"(?i)\b(api[_-]?key|secret|password|passwd|token)(\"?\s*[:=]\s*\"?)([^\s\"',;]{8,})"), r"\1\2[redacted]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "[redacted-key]"),
    # paths of other, unrelated projects on the laptop (e.g. from a directory listing)
    (re.compile(r"/Users/[^/\s]+/Desktop/(?!Tripwire-Hackathon-Playbook)[^\s'\"\)\]]*"), "[other-local-path]"),
]


def secret_values() -> list[str]:
    vals: set[str] = set()
    for name in (".env", ".env.cloud"):
        f = ROOT / name
        if not f.exists():
            continue
        for line in f.read_text().splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip("\"'")
            if not SECRET_KEY_RE.search(k) or len(v) < 6 or v in {"change-me", "localhost", "127.0.0.1"}:
                continue
            vals.add(v)
            for part in re.split(r"[:@/]", v):  # id:secret pairs, user@host, URL pieces
                if len(part) >= 12:
                    vals.add(part)
    tok = Path.home() / ".tripwire-guild-proxy-token"
    if tok.exists():
        vals.add(tok.read_text().strip())
    tun = ROOT / "var/run/tunnel_url"
    if tun.exists() and tun.read_text().strip():
        vals.add(tun.read_text().strip())
    return sorted(vals, key=len, reverse=True)  # longest first so a part never pre-empts the whole


def scrub(text: str, vals: list[str]) -> str:
    for v in vals:
        text = text.replace(v, "[redacted]")
    for rx, rep in GENERIC:
        text = rx.sub(rep, text)
    return text


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + f"\n… [{len(s) - n} more chars]"


def render(path: Path) -> list[str]:
    out: list[str] = []
    for line in path.open():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = d.get("type")
        if kind not in ("user", "assistant") or d.get("isMeta"):
            continue
        ts = (d.get("timestamp") or "")[:19].replace("T", " ")
        content = (d.get("message") or {}).get("content")
        parts = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
        for p in parts:
            t = p.get("type")
            if t == "text" and p.get("text", "").strip():
                who = "**User**" if kind == "user" else "**Claude**"
                out.append(f"### {ts} UTC · {who}\n\n{clip(p['text'].strip(), TEXT_MAX)}\n")
            elif t == "tool_use":
                inp = json.dumps(p.get("input", {}), ensure_ascii=False, indent=1)
                out.append(f"<details><summary>{ts} · tool call: {p.get('name')}</summary>\n\n```json\n{clip(inp, TOOL_INPUT_MAX)}\n```\n</details>\n")
            elif t == "tool_result":
                c = p.get("content")
                if isinstance(c, list):
                    c = "\n".join(x.get("text", "[image]") if isinstance(x, dict) else str(x) for x in c)
                out.append(f"<details><summary>{ts} · tool result</summary>\n\n```\n{clip(str(c or ''), TOOL_RESULT_MAX)}\n```\n</details>\n")
    return out


def main() -> int:
    out_path, sessions = Path(sys.argv[1]), [Path(a) for a in sys.argv[2:]]
    vals = secret_values()
    body = [
        "# Provenance: Bindu's Claude Code sessions (Tripwire, Oct 9 2026)\n",
        "Exported by `provenance/export.py`. Secrets, hosts, tokens, the tunnel URL and e-mail addresses are"
        " replaced with `[redacted]`-style markers; long tool inputs/results are clipped. Nothing else is edited.\n",
    ]
    for s in sessions:
        body.append(f"\n---\n\n## Session `{s.stem}`\n")
        body.extend(render(s))
    text = scrub("\n".join(body), vals)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text)
    leaks = sum(1 for v in vals if v in out_path.read_text())
    if leaks:
        out_path.unlink()
        print(f"REFUSED: {leaks} secret value(s) still present; output deleted")
        return 1
    print(f"ok: {len(text):,} chars, {len(vals)} secret values checked, 0 present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
