"""Extraction, sampling, export privacy, CLI."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from stopcoder import _store
from stopcoder._cli import main
from stopcoder._extract import stops_in, transcript_files
from stopcoder._store import State, sample


def _claude(path: Path, turns: list[tuple[str, str]]) -> Path:
    lines = []
    for k, (human, agent) in enumerate(turns):
        lines.append({"type": "user", "timestamp": f"2026-09-01T10:{k:02d}:00Z",
                      "message": {"role": "user", "content": human}})
        lines.append({"type": "user", "timestamp": f"2026-09-01T10:{k:02d}:10Z",
                      "message": {"content": [{"type": "tool_result", "content": "x"}]}})
        lines.append({"type": "assistant", "timestamp": f"2026-09-01T10:{k:02d}:30Z",
                      "message": {"model": "claude-opus-5", "content": [{"type": "text", "text": agent}]}})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return path


def _codex(path: Path, turns: list[tuple[str, str]]) -> Path:
    lines: list[dict] = [{"type": "session_meta", "payload": {}},
                         {"type": "turn_context", "payload": {"model": "gpt-5.6"}}]
    for k, (human, agent) in enumerate(turns):
        lines.append({"type": "event_msg", "timestamp": f"2026-07-01T09:{k:02d}:00Z",
                      "payload": {"type": "user_message", "message": human}})
        lines.append({"type": "event_msg", "timestamp": f"2026-07-01T09:{k:02d}:20Z",
                      "payload": {"type": "agent_message", "message": agent}})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return path


def test_claude_stops_skip_tool_results_and_system_text(tmp_path: Path) -> None:
    f = _claude(tmp_path / "p" / "s.jsonl", [("build it", "Done. Shall I push?"),
                                              ("<system-reminder>x</system-reminder>", "ignored"),
                                              ("yes", "Pushed.")])
    stops = stops_in(f)
    # the system-reminder is not a human turn, so it merges into turn 0's agent text
    assert len(stops) == 1
    s = stops[0]
    assert s.reply == "yes" and s.prompt == "build it" and s.model == "claude-opus-5"
    assert s.harness == "claude" and s.idle_seconds is not None


def test_codex_stops(tmp_path: Path) -> None:
    f = _codex(tmp_path / "r.jsonl", [("# AGENTS.md instructions", "-"), ("fix bug", "Fixed. Tests?"),
                                       ("add tests", "Added.")])
    stops = stops_in(f)
    assert [(s.prompt, s.reply) for s in stops] == [("fix bug", "add tests")]
    assert stops[0].harness == "codex" and stops[0].model == "gpt-5.6"


def test_discovery_excludes_subagents(tmp_path: Path) -> None:
    _claude(tmp_path / ".claude/projects/p/a.jsonl", [("a", "b"), ("c", "d")])
    _claude(tmp_path / ".claude/projects/p/a/subagents/x.jsonl", [("a", "b"), ("c", "d")])
    _codex(tmp_path / ".codex/sessions/2026/07/01/r.jsonl", [("a", "b"), ("c", "d")])
    names = sorted(p.name for p in transcript_files(tmp_path))
    assert names == ["a.jsonl", "r.jsonl"]
    _claude(tmp_path / "archive/host/claude/p/z.jsonl", [("a", "b"), ("c", "d")])
    _claude(tmp_path / "archive/host/claude/p/z/subagents/y.jsonl", [("a", "b"), ("c", "d")])
    names = sorted(p.name for p in transcript_files(tmp_path, [tmp_path / "archive"]))
    assert names == ["a.jsonl", "r.jsonl", "z.jsonl"]


def test_sample_caps_per_session(tmp_path: Path) -> None:
    f = _claude(tmp_path / "p" / "s.jsonl", [(f"q{k}", f"a{k}") for k in range(20)])
    g = _claude(tmp_path / "p" / "t.jsonl", [(f"q{k}", f"a{k}") for k in range(3)])
    picked = sample(stops_in(f) + stops_in(g), 50, seed=1)
    assert len(picked) == 3 + 2
    assert sum(s.path == str(f) for s in picked) == 3


def test_export_has_no_text_by_default(tmp_path: Path) -> None:
    f = _claude(tmp_path / "p" / "s.jsonl", [("SECRET prompt", "SECRET ending"), ("SECRET reply", "ok")])
    st = State.new(stops_in(f), 1, seed=0, path=tmp_path / "state.json")
    st.labels[_store.key(st.sample[0])] = {"label": "PICK", "note": "SECRET note", "seconds": 3.0}
    st.save()
    blob = json.dumps(st.export())
    assert "SECRET" not in blob and str(tmp_path) not in blob
    assert st.export()["items"][0]["label"] == "PICK"
    assert "SECRET reply" in json.dumps(st.export(include_text=True))


def test_state_resumes(tmp_path: Path) -> None:
    f = _claude(tmp_path / "p" / "s.jsonl", [("a", "b"), ("c", "d"), ("e", "f")])
    st = State.new(stops_in(f), 2, seed=0, path=tmp_path / "state.json")
    st.labels[_store.key(st.sample[0])] = {"label": "NUDGE"}
    st.save()
    again = State.load(tmp_path / "state.json")
    assert again is not None and again.done() == 1 and again.first_unlabelled() == 1


def test_note_alone_is_not_a_label(tmp_path: Path) -> None:
    f = _claude(tmp_path / "p" / "s.jsonl", [("a", "b"), ("c", "d"), ("e", "f")])
    st = State.new(stops_in(f), 2, seed=0, path=tmp_path / "state.json")
    st.labels[_store.key(st.sample[0])] = {"note": "hmm"}
    assert st.done() == 0 and st.first_unlabelled() == 0 and st.export()["items"] == []


def test_cli_status_without_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                  capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(_store, "STATE_FILE", tmp_path / "none.json")
    monkeypatch.setattr("stopcoder._cli.State.load", lambda path=tmp_path / "none.json": None)
    assert main(["status"]) == 0
    assert "No annotation started" in capsys.readouterr().out


def test_cli_annotate_refuses_without_tty(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
