"""Deliver an export: POST it to the study's endpoint, or leave a file to send by hand."""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any

# Set when the study's collection endpoint exists; $STOPCODER_ENDPOINT or --to override it.
DEFAULT_ENDPOINT = ""


def endpoint(override: str | None = None) -> str:
    return override or os.environ.get("STOPCODER_ENDPOINT", "") or DEFAULT_ENDPOINT


def post(payload: dict[str, Any], url: str, timeout: float = 30) -> str:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "stopcoder"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(errors="replace")[:500]


def write(payload: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1))
    return path
