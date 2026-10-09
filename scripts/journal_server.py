#!/usr/bin/env python3
"""Принимает правки со страницы журнала и записывает их в отчёт.

Слушает только этот компьютер. Страницу можно открыть файлом из «Документов»
или по адресу http://127.0.0.1:47655/
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import append_entry


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == "/health":
            self._send(200, b"ok", "text/plain; charset=utf-8")
            return
        if self.path.split("?", 1)[0] in {"/", "/index.html"}:
            page = append_entry.render(append_entry.load()).encode("utf-8")
            self._send(200, page, "text/html; charset=utf-8")
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/stop":
            self._json(200, {"status": "stopping"})
            threading.Timer(0.25, append_entry.stop_service).start()
            return
        if path != "/item":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > 8000:
            self._json(400, {"error": "плохой запрос"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self._json(400, {"error": "плохой запрос"})
            return
        try:
            status = append_entry.update_item(
                str(payload.get("id", "")),
                str(payload.get("title", "")),
                str(payload.get("detail", "")),
            )
        except append_entry.JournalError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(200, {"status": status})

    def _json(self, status: int, payload: dict) -> None:
        self._send(
            status,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def log_message(self, fmt: str, *args) -> None:
        if self.path.split("?", 1)[0] == "/health":
            return
        super().log_message(fmt, *args)


def main() -> None:
    try:
        server = ThreadingHTTPServer((append_entry.HOST, append_entry.PORT), Handler)
    except OSError as exc:
        raise SystemExit(f"журнал уже слушает порт или порт занят: {exc}") from exc
    server.serve_forever()


if __name__ == "__main__":
    main()
