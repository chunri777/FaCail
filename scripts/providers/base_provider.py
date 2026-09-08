from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class MarketDataProvider(ABC):
    """Interface for swappable FaCail market data providers."""

    @abstractmethod
    def get_daily_bars(self) -> list[dict[str, Any]]:
        """Return stock daily bars with enough history for MA and volume rules."""

    @abstractmethod
    def get_market_snapshot(self) -> dict[str, Any]:
        """Return broad-market state and index snapshots."""

    @abstractmethod
    def get_sectors(self) -> list[dict[str, Any]]:
        """Return sector breadth, trend, rank, and turnover data."""

    @abstractmethod
    def get_intraday_snapshots(self) -> list[dict[str, Any]]:
        """Return current session snapshots keyed by stock code."""

    @abstractmethod
    def get_holdings(self) -> list[dict[str, Any]]:
        """Return the user's current holdings with position metadata."""

    @abstractmethod
    def get_watchlist(self) -> list[dict[str, Any]]:
        """Return current watchlist records."""
