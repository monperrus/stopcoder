"""The codebook: what the human contributed when the agent stopped."""
from __future__ import annotations

CODEBOOK_VERSION = "1"

# key, name, one-line definition shown in the TUI
LABELS: list[tuple[str, str, str]] = [
    ("1", "NUDGE", "No new information: yes / continue / proceed / do next."),
    ("2", "POLL", "Asked for status or progress; no new information."),
    ("3", "PICK", "Chose among options the agent offered, or endorsed its recommendation."),
    ("4", "GOAL", "Gave goals, thresholds, priorities, scope or taste the agent could not know."),
    ("5", "CORRECT", "Corrected a misunderstanding, a mistake or a wrong direction."),
    ("6", "EXTERNAL", "Gave facts only you had, or approved money / push / publish / contact."),
    ("7", "NEWTASK", "Started an unrelated task or question."),
    ("0", "UNCLEAR", "Cannot tell from what is shown."),
]

BY_KEY = {k: n for k, n, _ in LABELS}
NAMES = [n for _, n, _ in LABELS]
