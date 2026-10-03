"""Curses screen: one stop at a time, one key to label it."""
from __future__ import annotations

import curses
import textwrap
import time
from typing import Any

from ._labels import BY_KEY, LABELS
from ._store import State, key

HELP = "1-7 label · 0 unclear · ↑↓ PgUp PgDn scroll · ← back · → skip · n note · ? codebook · q save & quit"


def _human_duration(s: float | None) -> str:
    if s is None:
        return "?"
    if s < 90:
        return f"{s:.0f}s"
    if s < 5400:
        return f"{s / 60:.0f} min"
    if s < 172800:
        return f"{s / 3600:.1f} h"
    return f"{s / 86400:.1f} d"


def _wrap(text: str, width: int) -> list[str]:
    out: list[str] = []
    for para in text.splitlines() or [""]:
        out.extend(textwrap.wrap(para, width, replace_whitespace=False, drop_whitespace=True) or [""])
    return out


class App:
    def __init__(self, scr: Any, state: State):
        self.scr, self.state = scr, state
        self.i = state.first_unlabelled()
        if self.i >= len(state.sample):
            self.i = 0
        self.scroll = -1  # -1: start at the bottom of the agent's ending
        self.shown_at = time.monotonic()
        self.flash = ""
        curses.curs_set(0)
        curses.use_default_colors()
        for n, c in enumerate((curses.COLOR_CYAN, curses.COLOR_YELLOW, curses.COLOR_GREEN, curses.COLOR_MAGENTA), 1):
            curses.init_pair(n, c, -1)

    # --- drawing ---------------------------------------------------------------------
    def put(self, y: int, x: int, s: str, attr: int = 0) -> None:
        h, w = self.scr.getmaxyx()
        if 0 <= y < h and x < w:
            try:
                self.scr.addnstr(y, x, s, max(0, w - x - 1), attr)
            except curses.error:
                pass

    def draw(self) -> None:
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        s = self.state.sample[self.i]
        lab = self.state.labels.get(key(s), {}).get("label")
        width = max(20, w - 6)

        head = (f" stopcoder  {self.i + 1}/{len(self.state.sample)}  ·  {self.state.done()} labelled  ·  "
                f"{s['harness']} · {s['model'] or '?'} · {s['started'][:10]} · "
                f"you replied after {_human_duration(s['idle_seconds'])}")
        self.put(0, 0, head.ljust(w), curses.A_REVERSE)
        if lab:
            self.put(0, max(0, w - len(lab) - 4), f" {lab} ", curses.A_REVERSE | curses.A_BOLD)

        prompt = _wrap(s["prompt"], width)
        reply = _wrap(s["reply"], width)
        p_lines = prompt[:3] + (["…"] if len(prompt) > 3 else [])
        r_max = max(3, min(len(reply), h // 4))
        r_lines = reply[:r_max] + (["…"] if len(reply) > r_max else [])
        fixed = 1 + 1 + len(p_lines) + 1 + 1 + len(r_lines) + 4
        box = max(3, h - fixed - 1)
        ending = _wrap(s["ending"], width)
        top_max = max(0, len(ending) - box)
        if self.scroll < 0 or self.scroll > top_max:
            self.scroll = top_max
        y = 2
        self.put(y, 1, "YOU ASKED", curses.color_pair(1) | curses.A_BOLD)
        y += 1
        for ln in p_lines:
            self.put(y, 3, ln, curses.A_DIM)
            y += 1
        more = f"  (lines {self.scroll + 1}-{min(len(ending), self.scroll + box)} of {len(ending)}, ↑↓ to scroll)" \
            if len(ending) > box else ""
        self.put(y, 1, "THE AGENT ENDED ITS TURN WITH" + more, curses.color_pair(2) | curses.A_BOLD)
        y += 1
        for ln in ending[self.scroll:self.scroll + box]:
            self.put(y, 3, ln)
            y += 1
        y += 1
        self.put(y, 1, "YOU THEN SAID", curses.color_pair(3) | curses.A_BOLD)
        y += 1
        for ln in r_lines:
            self.put(y, 3, ln, curses.A_BOLD)
            y += 1

        self.put(h - 4, 1, "What did your reply give the agent that it did not have?", curses.A_BOLD)
        x = 1
        for k, n, _ in LABELS:
            item = f"{k} {n}"
            attr = curses.A_REVERSE if n == lab else 0
            if x + len(item) + 2 >= w:
                break
            self.put(h - 3, x, item, attr | curses.color_pair(4))
            x += len(item) + 3
        self.put(h - 2, 1, self.flash or HELP, curses.A_DIM)
        self.flash = ""
        self.scr.refresh()

    def codebook(self) -> None:
        self.scr.erase()
        _, w = self.scr.getmaxyx()
        self.put(0, 0, " codebook: what did your reply contribute? ".ljust(w), curses.A_REVERSE)
        y = 2
        for k, n, d in LABELS:
            self.put(y, 2, f"{k}  {n:<9}", curses.A_BOLD | curses.color_pair(4))
            for ln in _wrap(d, max(20, w - 18)):
                self.put(y, 16, ln)
                y += 1
            y += 1
        self.put(y + 1, 2, "If two apply, pick the one the agent most needed. Any key to go back.", curses.A_DIM)
        self.scr.refresh()
        self.scr.getch()

    def note(self) -> None:
        h, w = self.scr.getmaxyx()
        self.put(h - 2, 1, " " * (w - 2))
        self.put(h - 2, 1, "note (Enter to save): ", curses.A_BOLD)
        curses.echo()
        curses.curs_set(1)
        try:
            raw = self.scr.getstr(h - 2, 23, 300)
        finally:
            curses.noecho()
            curses.curs_set(0)
        text = raw.decode(errors="replace").strip()
        k = key(self.state.sample[self.i])
        self.state.labels.setdefault(k, {})["note"] = text
        self.state.save()
        self.flash = "note saved"

    # --- loop ------------------------------------------------------------------------
    def go(self, i: int) -> None:
        self.i = max(0, min(len(self.state.sample) - 1, i))
        self.scroll = -1
        self.shown_at = time.monotonic()

    def run(self) -> None:
        while True:
            self.draw()
            ch = self.scr.getch()
            h, _ = self.scr.getmaxyx()
            if ch in (ord("q"), 27):
                self.state.save()
                return
            if ch == ord("?"):
                self.codebook()
            elif ch == ord("n"):
                self.note()
            elif ch == curses.KEY_UP:
                self.scroll = max(0, self.scroll - 1)
            elif ch == curses.KEY_DOWN:
                self.scroll += 1
            elif ch == curses.KEY_PPAGE:
                self.scroll = max(0, self.scroll - (h // 2))
            elif ch == curses.KEY_NPAGE:
                self.scroll += h // 2
            elif ch in (curses.KEY_LEFT, ord("b")):
                self.go(self.i - 1)
            elif ch in (curses.KEY_RIGHT, ord("s")):
                self.go(self.i + 1)
            elif 0 <= ch < 256 and chr(ch) in BY_KEY:
                k = key(self.state.sample[self.i])
                prev = self.state.labels.get(k, {})
                self.state.labels[k] = {**prev, "label": BY_KEY[chr(ch)],
                                        "seconds": round(time.monotonic() - self.shown_at, 1)}
                self.state.save()
                n = len(self.state.sample)
                order = [(self.i + d) % n for d in range(1, n)]
                nxt = next((j for j in order if not self.state.is_labelled(self.state.sample[j])), None)
                if nxt is None:
                    return
                self.go(nxt)


def annotate(state: State) -> None:
    curses.wrapper(lambda scr: App(scr, state).run())
