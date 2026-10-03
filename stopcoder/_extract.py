"""Find coding-agent transcripts on this machine and cut them into stops.

A stop is an agent end-of-turn followed by a human message. Claude Code transcripts
(~/.claude/projects/*/*.jsonl) and Codex rollouts (~/.codex/sessions/**/*.jsonl) are read.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

_SKIP_PREFIXES = (
    "<command-", "<local-command", "<system-reminder>", "<task-notification", "[Cross-session",
    "Caveat:", "<bash-", "[Request interrupted", "This session is being continued",
    "<environment_context", "# AGENTS.md", "<user_instructions",
)


@dataclass
class Stop:
    harness: str          # "claude" | "codex"
    path: str             # transcript file (local only, never exported)
    index: int            # turn index in the session
    session_turns: int
    project: str          # directory name of the project (local only)
    model: str
    started: str          # ISO timestamp of the turn's human message
    prompt: str           # human message that started the turn
    ending: str           # agent's last text in the turn
    reply: str            # next human message
    turn_seconds: float | None
    idle_seconds: float | None


def _ts(e: dict[str, Any]) -> datetime | None:
    t = e.get("timestamp")
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00")) if isinstance(t, str) else None
    except ValueError:
        return None


def _human_text(e: dict[str, Any]) -> str | None:
    if e.get("type") != "user" or e.get("isMeta") or e.get("isSidechain"):
        return None
    c = e.get("message", {}).get("content")
    if isinstance(c, list):
        if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
            return None
        c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    if not isinstance(c, str):
        return None
    s = c.strip()
    if not s or s.startswith(_SKIP_PREFIXES) or "<cross-session" in s[:200]:
        return None
    return s


def _turns_claude(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    turns: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    last_text, last_ts, model = "", None, ""
    for e in events:
        h = _human_text(e)
        if h is not None:
            if cur:
                turns.append({**cur, "end": last_ts, "last": last_text})
            cur = {"start": _ts(e), "human": h}
            last_text, last_ts = "", _ts(e)
        elif e.get("type") == "assistant" and not e.get("isSidechain"):
            msg = e.get("message", {})
            model = msg.get("model") or model
            last_ts = _ts(e) or last_ts
            for x in msg.get("content") or []:
                if isinstance(x, dict) and x.get("type") == "text" and x.get("text", "").strip():
                    last_text = x["text"]
    if cur:
        turns.append({**cur, "end": last_ts, "last": last_text})
    return turns, model


def _turns_codex(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    turns: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    last_text, last_ts, model = "", None, ""
    for e in events:
        p = e.get("payload") or {}
        if e.get("type") == "turn_context":
            model = p.get("model") or model
        if e.get("type") != "event_msg":
            continue
        kind = p.get("type")
        if kind == "user_message" and (p.get("message") or "").strip():
            msg = p["message"].strip()
            if msg.startswith(_SKIP_PREFIXES):
                continue
            if cur:
                turns.append({**cur, "end": last_ts, "last": last_text})
            cur = {"start": _ts(e), "human": msg}
            last_text, last_ts = "", _ts(e)
        elif kind == "agent_message":
            last_text, last_ts = p.get("message") or last_text, _ts(e) or last_ts
        elif kind == "task_complete":
            last_ts = _ts(e) or last_ts
    if cur:
        turns.append({**cur, "end": last_ts, "last": last_text})
    return turns, model


def _secs(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None:
        return None
    d = (b - a).total_seconds()
    return d if d >= 0 else None


def stops_in(path: Path) -> list[Stop]:
    """Every stop in one transcript; empty if it cannot be parsed."""
    events: list[dict[str, Any]] = []
    try:
        with path.open(errors="replace") as fh:
            for line in fh:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    if not events:
        return []
    codex = any(e.get("type") == "session_meta" for e in events[:5])
    turns, model = (_turns_codex if codex else _turns_claude)(events)
    out = []
    for i, t in enumerate(turns[:-1]):
        nxt = turns[i + 1]
        if not t["last"].strip():
            continue
        out.append(Stop(
            harness="codex" if codex else "claude", path=str(path), index=i, session_turns=len(turns),
            project=path.parent.name if not codex else "", model=model,
            started=t["start"].isoformat() if t["start"] else "",
            prompt=t["human"], ending=t["last"], reply=nxt["human"],
            turn_seconds=_secs(t["start"], t["end"]), idle_seconds=_secs(t["end"], nxt["start"]),
        ))
    return out


def transcript_files(home: Path | None = None, extra: Iterable[Path] = ()) -> Iterable[Path]:
    """Claude Code and Codex transcripts of this user, main sessions only (no subagents).

    `extra` directories (e.g. a synced archive of other machines) are searched recursively."""
    for d in extra:
        yield from (p for p in Path(d).rglob("*.jsonl") if "subagents" not in p.parts)
    home = home or Path(os.path.expanduser("~"))
    claude = home / ".claude" / "projects"
    if claude.is_dir():
        yield from (p for p in claude.glob("*/*.jsonl"))
    codex = home / ".codex" / "sessions"
    if codex.is_dir():
        yield from codex.rglob("*.jsonl")
