"""Look up the adapter for a provider name. Tests can override with `set_adapter`."""

from app.integrations.pos.base import POSAdapter

_overrides: dict[str, POSAdapter] = {}


def get_adapter(provider: str) -> POSAdapter:
    if provider in _overrides:
        return _overrides[provider]
    if provider == "clover":
        from app.integrations.pos.clover import CloverAdapter

        return CloverAdapter()
    if provider == "square":
        from app.integrations.pos.square import SquareAdapter

        return SquareAdapter()
    raise ValueError(f"Unknown POS provider '{provider}'")


def set_adapter(provider: str, adapter: POSAdapter | None) -> None:
    if adapter is None:
        _overrides.pop(provider, None)
    else:
        _overrides[provider] = adapter


POS_PROVIDERS = ("clover", "square")
