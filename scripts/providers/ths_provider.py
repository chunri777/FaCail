from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .base_provider import MarketDataProvider, ProviderUnavailable
from .mock_provider import load_local_user_config


class ThsProvider(MarketDataProvider):
    """Adapter for Tonghuashun QuantAPI or a local THS bridge."""

    id = "ths"
    label = "同花顺"

    def __init__(self, codes: list[str] | None = None) -> None:
        self.local_api_url = os.getenv("THS_LOCAL_API_URL", "").rstrip("/")
        self.token = os.getenv("THS_TOKEN") or os.getenv("THS_API_TOKEN") or os.getenv("IFIND_TOKEN")
        self.username = os.getenv("THS_USERNAME") or os.getenv("IFIND_USER")
        self.password = os.getenv("THS_PASSWORD") or os.getenv("IFIND_PASSWORD")
        self.codes = codes or self._codes_from_user_config()

    @staticmethod
    def sdk_available() -> bool:
        return bool(importlib.util.find_spec("iFinDPy") or importlib.util.find_spec("THS_iFinD"))

    def check_access(self) -> dict[str, Any]:
        checks = {
            "local_api_url": bool(self.local_api_url),
            "token": bool(self.token),
            "username_password": bool(self.username and self.password),
            "sdk_available": self.sdk_available(),
        }
        missing = []
        if not checks["local_api_url"] and not checks["sdk_available"]:
            missing.append("未检测到 THS_LOCAL_API_URL，也未安装同花顺 QuantAPI Python SDK。")
        if checks["sdk_available"] and not (checks["token"] or checks["username_password"]):
            missing.append("检测到 SDK 时，需要通过环境变量提供 THS_TOKEN，或 THS_USERNAME/THS_PASSWORD。")
        if self.local_api_url:
            health = self._request("/health", required=False)
            checks["local_api_health"] = bool(health)
            if not health:
                missing.append("THS_LOCAL_API_URL 已设置，但 /health 未返回可用状态。")
        if checks["sdk_available"] and not self.local_api_url:
            missing.append("已检测到 SDK 时，仍需提供 THS_LOCAL_API_URL 本地桥接或补充 SDK 字段映射后再生成真实 JSON。")
        return {
            "provider": self.id,
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "checks": checks,
            "ready": not missing and bool(checks.get("local_api_health")),
            "missing": missing,
        }

    def _codes_from_user_config(self) -> list[str]:
        local = load_local_user_config()
        codes = {item["code"] for item in local.get("holdings", []) if item.get("code")}
        codes.update(item["code"] for item in local.get("watchlist", []) if item.get("code"))
        env_codes = [item.strip() for item in os.getenv("FACAIL_CODES", "").split(",") if item.strip()]
        codes.update(env_codes)
        return sorted(codes)

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(self, path: str, params: dict[str, str] | None = None, required: bool = True) -> Any:
        if not self.local_api_url:
            if required:
                raise ProviderUnavailable("未配置 THS_LOCAL_API_URL。", ["THS_LOCAL_API_URL"])
            return None
        query = f"?{urlencode(params)}" if params else ""
        request = Request(f"{self.local_api_url}{path}{query}", headers=self._headers())
        try:
            with urlopen(request, timeout=8) as response:
                return json.loads(response.read().decode("utf-8"))
        except (OSError, URLError, json.JSONDecodeError) as exc:
            if required:
                raise ProviderUnavailable(f"同花顺本地接口不可用：{path}。", [str(exc)]) from exc
            return None

    def _require_ready(self) -> None:
        status = self.check_access()
        if status["ready"] and self.local_api_url:
            return
        raise ProviderUnavailable("同花顺真实数据源尚不可用。", status["missing"])

    def get_daily_bars(self) -> list[dict[str, Any]]:
        self._require_ready()
        return self._request("/daily-bars", {"codes": ",".join(self.codes)})

    def get_market_snapshot(self) -> dict[str, Any]:
        self._require_ready()
        return self._request("/market")

    def get_sectors(self) -> list[dict[str, Any]]:
        self._require_ready()
        return self._request("/sectors")

    def get_intraday_snapshots(self) -> list[dict[str, Any]]:
        self._require_ready()
        return self._request("/intraday", {"codes": ",".join(self.codes)})

    def get_holdings(self) -> list[dict[str, Any]]:
        local = load_local_user_config().get("holdings")
        if local:
            return local
        self._require_ready()
        return self._request("/holdings")

    def get_watchlist(self) -> list[dict[str, Any]]:
        local = load_local_user_config().get("watchlist")
        if local:
            return local
        self._require_ready()
        return self._request("/watchlist")
