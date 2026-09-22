from __future__ import annotations

import hashlib
import json
import random
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


class AstockHttp:
    """Small serial HTTP client with bounded retries and on-disk TTL cache."""

    def __init__(self, config: dict, root: Path) -> None:
        self.config = config
        self.cache_dir = root / config["cache_dir"]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.last_request: dict[str, float] = {}
        self.last_result: dict = {}
        self.stats = {"total_requests": 0, "tencent_requests": 0, "eastmoney_requests": 0,
                      "sina_requests": 0, "cache_hits": 0, "negative_cache_hits": 0,
                      "request_failures": 0}

    @staticmethod
    def _timestamp(value: float) -> str:
        return datetime.fromtimestamp(value, ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")

    def get(self, source: str, url: str, params: dict, ttl: int, encoding: str = "utf-8") -> str:
        full_url = f"{url}?{urlencode(params)}" if params else url
        key = hashlib.sha256(full_url.encode()).hexdigest()
        cache_path = self.cache_dir / f"{key}.json"
        failure_path = self.cache_dir / f"{key}.failed.json"
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if time.time() - cached["time"] < ttl:
                    self.stats["cache_hits"] += 1
                    self.last_result = {"source": source, "retrieved_at": self._timestamp(cached["time"]), "cache_hit": True}
                    return cached["body"]
            except (OSError, ValueError, KeyError):
                pass
        if failure_path.exists():
            try:
                failure = json.loads(failure_path.read_text(encoding="utf-8"))
                if time.time() - failure["time"] < self.config["http_negative_cache_seconds"]:
                    self.stats["negative_cache_hits"] += 1
                    raise RuntimeError(f"{source} recently failed: {failure['error']}")
            except (OSError, ValueError, KeyError):
                pass
        attempts = self.config["retries"] + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            interval = self.config["min_interval_seconds"].get(source, 0)
            wait = interval - (time.monotonic() - self.last_request.get(source, 0))
            if wait > 0:
                time.sleep(wait + (random.uniform(0.1, 0.3) if source == "eastmoney" else 0))
            self.last_request[source] = time.monotonic()
            self.stats["total_requests"] += 1
            self.stats[f"{source}_requests"] += 1
            request = Request(full_url, headers={"User-Agent": "Mozilla/5.0 FaCail/1.0", "Accept": "*/*"})
            try:
                with urlopen(request, timeout=self.config["timeout_seconds"]) as response:
                    body = response.read().decode(encoding)
                fetched_at = time.time()
                cache_path.write_text(json.dumps({"time": fetched_at, "body": body}), encoding="utf-8")
                failure_path.unlink(missing_ok=True)
                self.last_result = {"source": source, "retrieved_at": self._timestamp(fetched_at), "cache_hit": False}
                return body
            except HTTPError as exc:
                last_error = exc
                self.stats["request_failures"] += 1
                if exc.code not in {429, 500, 502, 503, 504}:
                    break
            except (OSError, URLError, UnicodeError) as exc:
                last_error = exc
                self.stats["request_failures"] += 1
            if attempt < attempts - 1:
                time.sleep(min(2 ** attempt, 4))
        failure_path.write_text(json.dumps({"time": time.time(), "error": str(last_error)}), encoding="utf-8")
        raise RuntimeError(f"{source} request failed: {last_error}")
