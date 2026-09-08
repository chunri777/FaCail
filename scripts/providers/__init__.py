from .base_provider import ProviderUnavailable
from .mock_provider import MockProvider
from .ths_provider import ThsProvider


def create_provider(provider_id: str):
    if provider_id == "mock":
        return MockProvider()
    if provider_id == "ths":
        return ThsProvider()
    raise ValueError(f"Unknown provider: {provider_id}")


__all__ = ["MockProvider", "ProviderUnavailable", "ThsProvider", "create_provider"]
