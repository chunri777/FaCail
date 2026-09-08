from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from providers.mock_provider import MockProvider


class Handler(BaseHTTPRequestHandler):
    provider = MockProvider()

    def _send(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._send({"ok": True, "source": "dev-ths-bridge"})
        elif path == "/market":
            self._send(self.provider.get_market_snapshot())
        elif path == "/sectors":
            self._send(self.provider.get_sectors())
        elif path == "/daily-bars":
            self._send(self.provider.get_daily_bars())
        elif path == "/intraday":
            self._send(self.provider.get_intraday_snapshots())
        elif path == "/holdings":
            self._send(self.provider.get_holdings())
        elif path == "/watchlist":
            self._send(self.provider.get_watchlist())
        else:
            self._send({"error": "not found"}, status=404)

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 5011), Handler)
    print("Dev THS bridge listening on http://127.0.0.1:5011")
    server.serve_forever()


if __name__ == "__main__":
    main()
