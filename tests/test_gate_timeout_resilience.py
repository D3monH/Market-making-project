from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import quant_mm.data as data


class FakeNetworkError(Exception):
    pass


class FakeRequestTimeout(FakeNetworkError):
    pass


def test_gate_load_markets_skips_currencies_and_retries(monkeypatch):
    attempts = {"count": 0}

    class FakeGate:
        def __init__(self, config):
            self.config = config
            self.id = "gate"
            self.has = {
                "fetchCurrencies": True,
                "fetchOHLCV": True,
                "fetchTicker": True,
            }

        def load_markets(self):
            attempts["count"] += 1
            # The project does not need Gate's /spot/currencies payload for
            # public OHLCV/ticker research. It must be disabled before load.
            assert self.has["fetchCurrencies"] is False
            if attempts["count"] < 3:
                raise FakeRequestTimeout("simulated timeout")
            return {"BTC/USDT": {"symbol": "BTC/USDT"}}

    fake_ccxt = SimpleNamespace(
        gate=FakeGate,
        NetworkError=FakeNetworkError,
        RequestTimeout=FakeRequestTimeout,
    )
    monkeypatch.setitem(sys.modules, "ccxt", fake_ccxt)
    monkeypatch.setattr(data.time, "sleep", lambda _seconds: None)

    exchange, resolved = data._create_exchange("gate")

    assert resolved == "gate"
    assert attempts["count"] == 3
    assert exchange.has["fetchCurrencies"] is False
    assert exchange.config["timeout"] == data.EXCHANGE_HTTP_TIMEOUT_MS
    assert exchange.config["enableRateLimit"] is True


def test_network_call_does_not_retry_non_network_errors(monkeypatch):
    class FakeBadRequest(Exception):
        pass

    fake_ccxt = SimpleNamespace(NetworkError=FakeNetworkError)
    monkeypatch.setitem(sys.modules, "ccxt", fake_ccxt)
    monkeypatch.setattr(data.time, "sleep", lambda _seconds: None)

    calls = {"count": 0}

    def bad_operation():
        calls["count"] += 1
        raise FakeBadRequest("bad parameter")

    with pytest.raises(FakeBadRequest, match="bad parameter"):
        data._network_call(bad_operation, label="test")

    assert calls["count"] == 1


def test_network_call_reports_clean_error_after_retry_exhaustion(monkeypatch):
    fake_ccxt = SimpleNamespace(NetworkError=FakeNetworkError)
    monkeypatch.setitem(sys.modules, "ccxt", fake_ccxt)
    monkeypatch.setattr(data.time, "sleep", lambda _seconds: None)

    calls = {"count": 0}

    def always_timeout():
        calls["count"] += 1
        raise FakeRequestTimeout("read timed out")

    with pytest.raises(RuntimeError, match="failed after 3 network attempts"):
        data._network_call(always_timeout, label="gate fetch_ohlcv")

    assert calls["count"] == 3
