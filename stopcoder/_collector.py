"""The study's receiving end: accept stopcoder exports over HTTP and store each as a file.

Runs behind a TLS reverse proxy. It keeps nothing but the submitted JSON (no IP address on disk),
rejects anything that is not a stopcoder export, and limits each client to a few posts per hour.

    stopcoder-collector --port 8097 --data ~/stopcoder-submissions
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

MAX_BYTES = 2_000_000
MAX_ITEMS = 2000
PER_HOUR = 20


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


class Handler(BaseHTTPRequestHandler):
    data_dir: Path
    recent: ClassVar[dict[str, deque[float]]] = defaultdict(deque)

    def _reply(self, code: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _client(self) -> str:
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def do_GET(self) -> None:
        if self.path.rstrip("/").endswith("/health"):
            self._reply(200, {"ok": True})
        else:
            self._reply(404, {"error": "POST a stopcoder export to /submit"})

    def do_POST(self) -> None:
        if not self.path.rstrip("/").endswith("/submit"):
            self._reply(404, {"error": "unknown path"})
            return
        now, q = time.time(), self.recent[self._client()]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= PER_HOUR:
            self._reply(429, {"error": "too many submissions, try again in an hour"})
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        if not 0 < n <= MAX_BYTES:
            self._reply(413, {"error": f"body must be 1..{MAX_BYTES} bytes"})
            return
        try:
            payload = json.loads(self.rfile.read(n))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._reply(400, {"error": "body is not JSON"})
            return
        reason = valid(payload)
        if reason:
            self._reply(422, {"error": reason})
            return
        q.append(now)
        sid = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"
        path = self.data_dir / f"{sid}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        os.chmod(tmp, 0o600)
        tmp.replace(path)
        self._reply(200, {"ok": True, "id": sid, "labelled": len(payload["items"])})

    def log_message(self, fmt: str, *args: Any) -> None:  # no client addresses in the log
        sys.stderr.write(f"{self.log_date_time_string()} {fmt % args}\n".replace(self.address_string(), "-"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="stopcoder-collector", description="Receive stopcoder exports.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8097)
    p.add_argument("--data", default=str(Path.home() / "stopcoder-submissions"))
    a = p.parse_args(argv)
    Handler.data_dir = Path(a.data)
    Handler.data_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(Handler.data_dir, 0o700)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
