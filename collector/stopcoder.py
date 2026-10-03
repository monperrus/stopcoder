#!/usr/bin/python3
"""stopcoder collector: POST a stopcoder export (JSON) here; GET returns {"ok": true}.

Stores each valid export as one file, mode 600, outside the web root. Keeps nothing else: no IP
address, no headers. At most DAILY_CAP submissions per UTC day. Source:
https://github.com/monperrus/stopcoder/blob/main/collector/stopcoder.py

Deployed as a CGI script at https://api.monperrus.com/stopcoder (stdlib only, single file).
"""
from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA = Path(os.environ.get("STOPCODER_DATA", "/home/www-data/stopcoder-submissions"))
MAX_BYTES = 2_000_000
MAX_ITEMS = 2000
DAILY_CAP = 300


def valid(payload: Any) -> str | None:
    """None if the payload is a stopcoder export, else the reason it is not."""
    if not isinstance(payload, dict) or payload.get("tool") != "stopcoder":
        return "not a stopcoder export"
    items = payload.get("items")
    if not isinstance(items, list) or not 0 < len(items) <= MAX_ITEMS:
        return "items must be a non-empty list"
    if not all(isinstance(i, dict) and isinstance(i.get("label"), str) for i in items):
        return "every item needs a label"
    return None


def reply(status: int, body: dict[str, Any]) -> None:
    sys.stdout.write(f"Status: {status}\nContent-Type: application/json\n\n{json.dumps(body)}\n")


def store(payload: dict[str, Any], now: datetime) -> str:
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    sid = f"{now:%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"
    tmp = DATA / f".{sid}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(payload, fh)
    tmp.replace(DATA / f"{sid}.json")
    return sid


def main() -> None:
    method = os.environ.get("REQUEST_METHOD", "GET")
    if method == "GET":
        reply(200, {"ok": True, "post": "a stopcoder export, see https://github.com/monperrus/stopcoder"})
        return
    if method != "POST":
        reply(405, {"error": "POST or GET only"})
        return
    try:
        n = int(os.environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        n = 0
    if not 0 < n <= MAX_BYTES:
        reply(413, {"error": f"body must be 1..{MAX_BYTES} bytes"})
        return
    try:
        payload = json.loads(sys.stdin.buffer.read(n))
    except (json.JSONDecodeError, UnicodeDecodeError):
        reply(400, {"error": "body is not JSON"})
        return
    reason = valid(payload)
    if reason:
        reply(422, {"error": reason})
        return
    now = datetime.now(timezone.utc)
    if DATA.is_dir() and len(list(DATA.glob(f"{now:%Y%m%d}T*.json"))) >= DAILY_CAP:
        reply(429, {"error": "daily limit reached, try again tomorrow"})
        return
    sid = store(payload, now)
    reply(200, {"ok": True, "id": sid, "labelled": len(payload["items"])})


if __name__ == "__main__":
    main()
