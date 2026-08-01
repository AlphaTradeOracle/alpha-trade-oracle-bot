"""Unit tests for leverage base matching."""

from __future__ import annotations

from app.market_data.leverage_coverage import base_has_leverage, normalize_base


def test_normalize_xbt_to_btc() -> None:
    assert normalize_base("xbt") == "BTC"


def test_base_has_leverage_direct() -> None:
    assert base_has_leverage("BTC", {"BTC", "ETH"})
    assert not base_has_leverage("SOL", {"BTC", "ETH"})


def test_base_has_leverage_hyperliquid_prefixes() -> None:
    assert base_has_leverage("PEPE", {"1000PEPE"})
    assert base_has_leverage("BONK", {"KBONK", "ETH"})
