"""Find coding-agent transcripts on this machine and cut them into stops.

A stop is an agent end-of-turn followed by a human message. Claude Code transcripts
(~/.claude/projects/*/*.jsonl), Codex rollouts (~/.codex/sessions/**/*.jsonl) and agentknit
journals (~/.local/share/agent_probe/<model>/*_journal.jsonl) are read.
"""
from __future__ import annotations

import ast
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
    harness: str          # "claude" | "codex" | "agentknit"
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
    t = e.get("timestamp") or e.get("ts")  # agentknit journals use "ts"
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
        if kind == "item_completed":  # codex >= 0.157: messages are items, not user_/agent_message
            item = p.get("item") or {}
            text = "\n".join(c.get("text", "") for c in item.get("content") or [] if isinstance(c, dict)).strip()
            if item.get("type") == "UserMessage" and text:
                kind, p = "user_message", {"message": text}
            elif item.get("type") == "AgentMessage" and text:
                kind, p = "agent_message", {"message": text}
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
            last_text = (p.get("last_agent_message") or "").strip() or last_text
    if cur:
        turns.append({**cur, "end": last_ts, "last": last_text})
    return turns, model


def _turns_agentknit(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """agentknit journal: turn_start carries the task; messages hold repr()'d chat messages.

    A turn started by an automated inbox post is kept as a turn (the agent worked) but marked,
    so the stop before it is not counted as a human reply."""
    turns: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    last_text, last_ts = "", None
    for e in events:
        kind = e.get("type")
        if kind == "turn_start":
            if cur:
                turns.append({**cur, "end": last_ts, "last": last_text})
            task = (e.get("task") or "").strip()
            cur = {"start": _ts(e), "human": task, "auto": task.startswith("📬") or "not typed by the operator" in task}
            last_text, last_ts = "", _ts(e)
        elif kind == "message":
            m = e.get("msg")
            if isinstance(m, str):
                try:
                    m = ast.literal_eval(m)
                except (ValueError, SyntaxError):
                    continue
            if isinstance(m, dict) and m.get("role") == "assistant" and isinstance(m.get("content"), str) \
                    and m["content"].strip():
                last_text, last_ts = m["content"], _ts(e) or last_ts
        elif kind == "turn_end":
            last_ts = _ts(e) or last_ts
    if cur:
        turns.append({**cur, "end": last_ts, "last": last_text})
    return turns, ""


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
    head = [e.get("type") for e in events[:5]]
    harness = "codex" if "session_meta" in head else "agentknit" if "turn_start" in head else "claude"
    parse = {"codex": _turns_codex, "agentknit": _turns_agentknit, "claude": _turns_claude}[harness]
    turns, model = parse(events)
    if harness == "agentknit":
        model = path.parent.name  # journals live under agent_probe/<model>/
    out = []
    for i, t in enumerate(turns[:-1]):
        nxt = turns[i + 1]
        if not t["last"].strip() or nxt.get("auto"):
            continue
        out.append(Stop(
            harness=harness, path=str(path), index=i, session_turns=len(turns),
            project=path.parent.name if harness == "claude" else "", model=model,
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
    agentknit = home / ".local" / "share" / "agent_probe"
    if agentknit.is_dir():
        yield from agentknit.glob("*/*_journal.jsonl")
