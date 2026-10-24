from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlencode


LIVE_BASE_URL = "https://api.gateio.ws/api/v4"
TESTNET_BASE_URL = "https://api-testnet.gateapi.io/api/v4"


@dataclass(frozen=True)
class GateExecutionConfig:
    mode: Literal["paper", "testnet", "live"] = "paper"
    account: str = "spot"
    api_key_env: str = "GATE_API_KEY"
    api_secret_env: str = "GATE_API_SECRET"
    live_confirmation_env: str = "ALLOW_GATE_LIVE_TRADING"

    @property
    def base_url(self) -> str:
        return TESTNET_BASE_URL if self.mode == "testnet" else LIVE_BASE_URL


def sign_gate_request(api_key: str, api_secret: str, method: str, request_path: str, query: str = "", body: str = "", timestamp: str | None = None) -> dict[str, str]:
    ts = timestamp or str(int(time.time()))
    payload_hash = hashlib.sha512(body.encode("utf-8")).hexdigest()
    sign_string = f"{method.upper()}\n{request_path}\n{query}\n{payload_hash}\n{ts}"
    signature = hmac.new(api_secret.encode("utf-8"), sign_string.encode("utf-8"), hashlib.sha512).hexdigest()
    return {"KEY": api_key, "Timestamp": ts, "SIGN": signature}


def build_spot_limit_order(symbol: str, side: str, amount: float, price: float, account: str = "spot") -> dict:
    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    return {
        "text": f"t-mm-{int(time.time() * 1000)}",
        "currency_pair": symbol.replace("/", "_").upper(),
        "type": "limit",
        "account": account,
        "side": side,
        "amount": format(amount, ".12g"),
        "price": format(price, ".12g"),
        "time_in_force": "gtc",
    }


class GateOrderExecutor:
    """Private Gate REST adapter for a later testnet/live execution phase.

    gate_paper.py never calls this class. Paper mode blocks private requests, and live
    mode additionally requires an explicit environment confirmation.
    """

    def __init__(self, cfg: GateExecutionConfig):
        self.cfg = cfg

    def _credentials(self) -> tuple[str, str]:
        key = os.getenv(self.cfg.api_key_env, "")
        secret = os.getenv(self.cfg.api_secret_env, "")
        if not key or not secret:
            raise RuntimeError("Gate API credentials are missing from environment variables")
        return key, secret

    def _assert_private_enabled(self) -> None:
        if self.cfg.mode == "paper":
            raise RuntimeError("private Gate requests are disabled in paper mode")
        if self.cfg.mode == "live" and os.getenv(self.cfg.live_confirmation_env) != "YES_I_UNDERSTAND":
            raise RuntimeError(
                f"live trading blocked: set {self.cfg.live_confirmation_env}=YES_I_UNDERSTAND only after paper/testnet validation"
            )

    def _request(self, method: str, endpoint: str, query_params: dict | None = None, payload: dict | None = None) -> dict | list:
        self._assert_private_enabled()
        import requests

        key, secret = self._credentials()
        query = urlencode(query_params or {})
        body = json.dumps(payload, separators=(",", ":")) if payload is not None else ""
        request_path = "/api/v4" + endpoint
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        headers.update(sign_gate_request(key, secret, method, request_path, query=query, body=body))
        url = self.cfg.base_url + endpoint + (f"?{query}" if query else "")
        response = requests.request(method.upper(), url, headers=headers, data=body or None, timeout=10)
        response.raise_for_status()
        return response.json()

    def place_limit_order(self, symbol: str, side: str, amount: float, price: float) -> dict:
        payload = build_spot_limit_order(symbol, side, amount, price, self.cfg.account)
        return self._request("POST", "/spot/orders", payload=payload)

    def cancel_order(self, order_id: str, symbol: str) -> dict:
        pair = symbol.replace("/", "_").upper()
        return self._request(
            "DELETE",
            f"/spot/orders/{order_id}",
            query_params={"currency_pair": pair, "account": self.cfg.account},
        )

    def cancel_all(self, symbol: str) -> list:
        pair = symbol.replace("/", "_").upper()
        return self._request(
            "DELETE",
            "/spot/orders",
            query_params={"currency_pair": pair, "account": self.cfg.account},
        )
