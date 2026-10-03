"""Sample stops, keep annotation progress on disk, and build the export.

Progress lives in ~/.local/state/stopcoder/annotation.json on the annotator's machine; it holds
transcript text and never leaves the machine. The export holds labels and numbers only, unless
the annotator opts in to including text.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import random
import secrets
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._extract import Stop, stops_in, transcript_files
from ._labels import CODEBOOK_VERSION

STATE_DIR = Path(os.environ.get("STOPCODER_STATE", Path.home() / ".local" / "state" / "stopcoder"))
STATE_FILE = STATE_DIR / "annotation.json"
PER_SESSION_CAP = 3


def collect(files: list[Path], progress: Callable[[int, int], None] | None = None) -> list[Stop]:
    out: list[Stop] = []
    for k, f in enumerate(files):
        out.extend(stops_in(f))
        if progress:
            progress(k + 1, len(files))
    return out


def sample(stops: list[Stop], n: int, seed: int) -> list[Stop]:
    """Uniform random sample, at most PER_SESSION_CAP stops from any one session."""
    rng = random.Random(seed)
    pool = stops[:]
    rng.shuffle(pool)
    taken: dict[str, int] = defaultdict(int)
    out = []
    for s in pool:
        if taken[s.path] >= PER_SESSION_CAP:
            continue
        taken[s.path] += 1
        out.append(s)
        if len(out) == n:
            break
    return out


def key(s: dict[str, Any]) -> str:
    return f"{s['path']}#{s['index']}"


class State:
    def __init__(self, data: dict[str, Any], path: Path = STATE_FILE):
        self.data, self.path = data, path

    @classmethod
    def load(cls, path: Path = STATE_FILE) -> State | None:
        try:
            return cls(json.loads(path.read_text()), path)
        except (OSError, json.JSONDecodeError):
            return None

    @classmethod
    def new(cls, stops: list[Stop], total: int, seed: int, path: Path = STATE_FILE) -> State:
        return cls({
            "codebook": CODEBOOK_VERSION, "created": _now(), "seed": seed, "salt": secrets.token_hex(16),
            "corpus_stops": total, "sample": [asdict(s) for s in stops], "labels": {},
        }, path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    @property
    def sample(self) -> list[dict[str, Any]]:
        return self.data["sample"]

    @property
    def labels(self) -> dict[str, dict[str, Any]]:
        return self.data["labels"]

    def is_labelled(self, s: dict[str, Any]) -> bool:
        return "label" in self.labels.get(key(s), {})

    def first_unlabelled(self) -> int:
        for i, s in enumerate(self.sample):
            if not self.is_labelled(s):
                return i
        return len(self.sample)

    def done(self) -> int:
        return sum(self.is_labelled(s) for s in self.sample)

    def export(self, include_text: bool = False, annotator: str = "") -> dict[str, Any]:
        """Labels plus per-stop numbers. Text only with include_text."""
        salt = self.data["salt"]
        items = []
        for s in self.sample:
            lab = self.labels.get(key(s), {})
            if "label" not in lab:
                continue
            item = {
                "id": hashlib.sha256(f"{salt}{key(s)}".encode()).hexdigest()[:16],
                "session": hashlib.sha256(f"{salt}{s['path']}".encode()).hexdigest()[:12],
                "harness": s["harness"], "model": s["model"], "month": s["started"][:7],
                "turn_index": s["index"], "session_turns": s["session_turns"],
                "turn_seconds": s["turn_seconds"], "idle_seconds": s["idle_seconds"],
                "prompt_chars": len(s["prompt"]), "ending_chars": len(s["ending"]),
                "reply_chars": len(s["reply"]), "ending_has_question": "?" in s["ending"][-400:],
                "label": lab["label"], "seconds_to_label": lab.get("seconds"),
            }
            if include_text:
                item.update(prompt=s["prompt"][:2000], ending=s["ending"][-3000:], reply=s["reply"][:2000],
                            note=lab.get("note", ""))
            items.append(item)
        return {
            "tool": "stopcoder", "codebook": CODEBOOK_VERSION, "exported": _now(), "annotator": annotator,
            "includes_text": include_text, "corpus_stops": self.data["corpus_stops"],
            "sampled": len(self.sample), "labelled": len(items), "platform": platform.system(), "items": items,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build(n: int, seed: int | None = None, home: Path | None = None, extra: list[Path] | None = None,
          progress: Callable[[int, int], None] | None = None) -> State:
    files = list(dict.fromkeys(transcript_files(home, extra or [])))
    stops = collect(files, progress)
    seed = seed if seed is not None else secrets.randbelow(2**31)
    return State.new(sample(stops, n, seed), len(stops), seed)
