from .base_provider import ProviderUnavailable
from .astock_provider import AstockProvider
from .mock_provider import MockProvider
from .ths_provider import ThsProvider


def create_provider(provider_id: str):
    if provider_id == "mock":
        return MockProvider()
    if provider_id == "ths":
        return ThsProvider()
    if provider_id == "astock":
        return AstockProvider()
    raise ValueError(f"Unknown provider: {provider_id}")


__all__ = ["AstockProvider", "MockProvider", "ProviderUnavailable", "ThsProvider", "create_provider"]
