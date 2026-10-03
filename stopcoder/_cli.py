"""stopcoder command line."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from . import __version__
from ._labels import NAMES
from ._send import endpoint, post, write
from ._store import STATE_DIR, STATE_FILE, State, build

INTRO = """\
stopcoder: label why your coding agent stopped.

It samples {n} moments from your own Claude Code / Codex history where the agent ended
its turn and you replied, and asks you one question per moment: what did your reply
give the agent that it did not have? About 10 seconds each.

Everything stays on this machine until you choose to send. By default only labels and
numbers (lengths, timings, model names) are sent, never text.
"""


def _progress(k: int, n: int) -> None:
    if k == n or k % 25 == 0:
        print(f"\r  reading transcripts {k}/{n}", end="", file=sys.stderr, flush=True)


def _summary(state: State) -> str:
    c = Counter(v["label"] for v in state.labels.values() if "label" in v)
    done = state.done()
    lines = [f"{done}/{len(state.sample)} labelled (sampled from {state.data['corpus_stops']} stops)"]
    for n in NAMES:
        if c[n]:
            lines.append(f"  {n:<9} {c[n]:3}  {100 * c[n] / max(done, 1):3.0f}%")
    return "\n".join(lines)


def _ask(q: str, choices: str) -> str:
    while True:
        a = input(f"{q} [{choices}] ").strip().lower()
        if a and a[0] in choices:
            return a[0]


def _deliver(state: State, include_text: bool, to: str | None, annotator: str) -> int:
    payload = state.export(include_text=include_text, annotator=annotator)
    url = endpoint(to)
    out = STATE_DIR / f"stopcoder-export{'-with-text' if include_text else ''}.json"
    write(payload, out)
    if not url:
        print(f"Saved {out}\nNo collection endpoint is configured: send that file to the study team.")
        return 0
    try:
        post(payload, url)
    except OSError as e:
        print(f"Sending failed ({e}). The export is saved at {out}: send it by hand.", file=sys.stderr)
        return 1
    print(f"Sent {payload['labelled']} labels. A copy is in {out}. Thank you.")
    return 0


def cmd_annotate(a: argparse.Namespace) -> int:
    from ._tui import annotate

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("stopcoder needs an interactive terminal.", file=sys.stderr)
        return 2
    state = State.load()
    if state is None or a.restart:
        print(INTRO.format(n=a.n))
        state = build(a.n, a.seed, extra=[Path(d) for d in a.from_dirs], progress=_progress)
        print(file=sys.stderr)
        if not state.sample:
            print("No agent stops found in ~/.claude/projects or ~/.codex/sessions.", file=sys.stderr)
            return 1
        state.save()
        input(f"Found {state.data['corpus_stops']} stops; sampled {len(state.sample)}. Press Enter to start.")
    annotate(state)
    print(_summary(state))
    if state.done() < len(state.sample):
        print("Run stopcoder again to continue where you left off.")
        return 0
    a2 = _ask("Send the results? l = labels and numbers only, t = also the text you saw, n = not now", "ltn")
    if a2 == "n":
        print("Nothing sent. Run `stopcoder send` later.")
        return 0
    return _deliver(state, a2 == "t", a.to, a.annotator)


def cmd_status(a: argparse.Namespace) -> int:
    state = State.load()
    if state is None:
        print("No annotation started. Run `stopcoder`.")
        return 0
    if a.json:
        print(json.dumps({"sampled": len(state.sample), "labelled": state.done(),
                          "labels": Counter(v["label"] for v in state.labels.values() if "label" in v)}))
    else:
        print(_summary(state))
    return 0


def cmd_export(a: argparse.Namespace) -> int:
    state = State.load()
    if state is None:
        print("No annotation started. Run `stopcoder`.", file=sys.stderr)
        return 1
    payload = state.export(include_text=a.include_text, annotator=a.annotator)
    if a.output:
        write(payload, Path(a.output))
    else:
        json.dump(payload, sys.stdout, indent=1)
        print()
    return 0


def cmd_send(a: argparse.Namespace) -> int:
    state = State.load()
    if state is None:
        print("No annotation started. Run `stopcoder`.", file=sys.stderr)
        return 1
    return _deliver(state, a.include_text, a.to, a.annotator)


def cmd_grade(a: argparse.Namespace) -> int:
    from ._grade import complete, grade, load_grades, state_path

    items_path = Path(a.items)
    try:
        items = json.loads(items_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"Cannot read items file {items_path}: {e}", file=sys.stderr)
        return 1
    if not a.export:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            print("stopcoder grade needs an interactive terminal (or --export).", file=sys.stderr)
            return 2
        grade(items_path)
    grades = load_grades(items_path)
    done = sum(complete(grades.get(it["id"])) for it in items)
    if a.export:
        print(json.dumps({"items": str(items_path), "graded": done, "total": len(items), "grades": grades}, indent=1))
    else:
        print(f"{done}/{len(items)} graded, saved in {state_path(items_path)}")
    return 0


def cmd_reset(a: argparse.Namespace) -> int:
    if STATE_FILE.exists() and (a.yes or _ask(f"Delete {STATE_FILE} and all labels?", "yn") == "y"):
        STATE_FILE.unlink()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="stopcoder", description="Label why your coding agent stopped.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("-n", type=int, default=50, help="stops to sample (default 50)")
    p.add_argument("--seed", type=int, help="sampling seed (default random)")
    p.add_argument("--restart", action="store_true", help="draw a new sample, dropping current progress")
    p.add_argument("--from", dest="from_dirs", action="append", default=[], metavar="DIR",
                   help="also read transcripts under DIR (repeatable), e.g. an archive of other machines")
    p.add_argument("--to", help="collection endpoint URL (default $STOPCODER_ENDPOINT)")
    p.add_argument("--annotator", default="", help="name or pseudonym recorded in the export")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("status", help="progress and label counts")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("export", help="write the export JSON")
    s.add_argument("-o", "--output")
    s.add_argument("--include-text", action="store_true")
    s = sub.add_parser("send", help="send the export to the study")
    s.add_argument("--include-text", action="store_true")
    s = sub.add_parser("grade", help="grade supervisor replies against your real reply (items file from the study)")
    s.add_argument("items", help="JSON list of {id, prompt, ending, reply, supervisor}")
    s.add_argument("--export", action="store_true", help="print the grades as JSON instead of grading")
    s = sub.add_parser("reset", help="delete progress")
    s.add_argument("-y", "--yes", action="store_true")
    a = p.parse_args(argv)
    return {"status": cmd_status, "export": cmd_export, "send": cmd_send, "reset": cmd_reset,
            "grade": cmd_grade}.get(
        a.cmd or "", cmd_annotate)(a)


if __name__ == "__main__":
    sys.exit(main())
