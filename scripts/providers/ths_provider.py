from __future__ import annotations

from typing import Any

from .base_provider import MarketDataProvider


class ThsProvider(MarketDataProvider):
    """Reserved adapter for Tonghuashun QuantAPI or a local THS bridge."""

    def get_daily_bars(self) -> list[dict[str, Any]]:
        raise NotImplementedError("THS provider is reserved for a later integration.")

    def get_holdings(self) -> list[dict[str, Any]]:
        raise NotImplementedError("THS provider is reserved for a later integration.")

    def get_watchlist(self) -> list[dict[str, Any]]:
        raise NotImplementedError("THS provider is reserved for a later integration.")
