import os
import base64
import logging
import httpx

logger = logging.getLogger(__name__)

DEMO_KEY = os.getenv("T212_DEMO_KEY", "")
DEMO_SECRET = os.getenv("T212_DEMO_SECRET", "")
DEMO_BASE = os.getenv("T212_DEMO_BASE", "https://demo.trading212.com/api/v0")

LIVE_KEY = os.getenv("T212_LIVE_KEY", "")
LIVE_SECRET = os.getenv("T212_LIVE_SECRET", "")
LIVE_BASE = os.getenv("T212_LIVE_BASE", "https://live.trading212.com/api/v0")


def _basic_auth(key: str, secret: str) -> str:
    token = base64.b64encode(f"{key}:{secret}".encode()).decode()
    return f"Basic {token}"


class Trading212Client:
    def __init__(self, mode: str = "demo"):
        self.mode = mode
        if mode == "live":
            self.base = LIVE_BASE
            self.auth = _basic_auth(LIVE_KEY, LIVE_SECRET)
        else:
            self.base = DEMO_BASE
            self.auth = _basic_auth(DEMO_KEY, DEMO_SECRET)

    def _headers(self) -> dict:
        return {
            "Authorization": self.auth,
            "Content-Type": "application/json",
        }

    async def get_account(self) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{self.base}/equity/account/cash", headers=self._headers())
            r.raise_for_status()
            return r.json()

    async def get_positions(self) -> list:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{self.base}/equity/positions", headers=self._headers())
            r.raise_for_status()
            return r.json()

    async def place_limit_order(self, symbol: str, quantity: int, limit_price: float) -> dict:
        payload = {
            "symbol": f"{symbol}_US_EQ",
            "quantity": quantity,
            "limitPrice": limit_price,
            "timeValidity": "DAY",
        }
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{self.base}/equity/orders/limit",
                headers=self._headers(),
                json=payload,
            )
            r.raise_for_status()
            return r.json()

    async def place_stop_order(self, symbol: str, quantity: int, stop_price: float) -> dict:
        payload = {
            "symbol": f"{symbol}_US_EQ",
            "quantity": -quantity,
            "stopPrice": stop_price,
            "timeValidity": "GTC",
        }
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{self.base}/equity/orders/stop",
                headers=self._headers(),
                json=payload,
            )
            r.raise_for_status()
            return r.json()

    async def close_position(self, symbol: str) -> dict:
        positions = await self.get_positions()
        qty = 0
        for p in positions:
            ticker = p.get("ticker", "").replace("_US_EQ", "")
            if ticker == symbol:
                qty = p.get("quantity", 0)
                break
        if qty <= 0:
            return {"error": f"No open position found for {symbol}"}
        payload = {
            "symbol": f"{symbol}_US_EQ",
            "quantity": -qty,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{self.base}/equity/orders/market",
                headers=self._headers(),
                json=payload,
            )
            r.raise_for_status()
            return r.json()

    async def get_orders(self) -> list:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{self.base}/equity/orders", headers=self._headers())
            r.raise_for_status()
            return r.json()
