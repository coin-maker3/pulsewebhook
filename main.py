import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from risk import RiskManager
from trading212 import Trading212Client
from notifier import notify_entry, notify_exit, notify_daily_summary

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="PulseWebhook", version="1.0.0")

TRADES_FILE = Path("trades.json")
MODE_FILE = Path("mode.txt")
risk = RiskManager()


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_trades() -> list:
    if TRADES_FILE.exists():
        try:
            return json.loads(TRADES_FILE.read_text())
        except Exception:
            return []
    return []


def _save_trades(trades: list):
    TRADES_FILE.write_text(json.dumps(trades, indent=2, default=str))


def _get_mode() -> str:
    if MODE_FILE.exists():
        return MODE_FILE.read_text().strip()
    return os.getenv("MODE", "demo")


def _set_mode(mode: str):
    MODE_FILE.write_text(mode)


def _get_client() -> Trading212Client:
    return Trading212Client(mode=_get_mode())


def _open_positions(trades: list) -> dict:
    """Return dict of symbol -> trade for currently open positions."""
    open_pos = {}
    for t in trades:
        sym = t.get("symbol")
        if t.get("status") == "open":
            open_pos[sym] = t
        elif t.get("status") in ("closed", "stopped"):
            open_pos.pop(sym, None)
    return open_pos


def _trades_today(trades: list) -> list:
    today = datetime.utcnow().date()
    return [t for t in trades if t.get("open_time", "").startswith(str(today))]


def _daily_pnl(trades: list) -> float:
    today_trades = _trades_today(trades)
    return sum(t.get("pnl", 0) for t in today_trades if t.get("status") in ("closed", "stopped"))


# ── webhook models ────────────────────────────────────────────────────────────

class WebhookSignal(BaseModel):
    action: str
    symbol: str
    price: float
    stop: Optional[float] = None
    target: Optional[float] = None
    confidence: Optional[int] = None
    reason: Optional[str] = None
    session: Optional[str] = "regular"
    change_pct: Optional[float] = 0.0
    relative_volume: Optional[float] = 0.0


class ModeSwitch(BaseModel):
    mode: str


# ── endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "mode": _get_mode(), "time": datetime.utcnow().isoformat()}


@app.get("/status")
async def status():
    trades = _load_trades()
    open_pos = _open_positions(trades)
    today = _trades_today(trades)
    daily_pnl = _daily_pnl(trades)

    portfolio_value = None
    try:
        client = _get_client()
        account = await client.get_account()
        portfolio_value = account.get("total", account.get("cash"))
    except Exception as e:
        logger.warning("Could not fetch account: %s", e)

    return {
        "mode": _get_mode(),
        "open_positions": list(open_pos.keys()),
        "open_position_count": len(open_pos),
        "trades_today": len(today),
        "daily_pnl": round(daily_pnl, 2),
        "portfolio_value": portfolio_value,
        "total_trades": len(trades),
    }


@app.get("/trades")
async def get_trades():
    return _load_trades()


@app.post("/mode")
async def set_mode(body: ModeSwitch):
    if body.mode not in ("demo", "live"):
        raise HTTPException(400, "mode must be 'demo' or 'live'")
    _set_mode(body.mode)
    logger.info("Mode switched to %s", body.mode)
    return {"mode": body.mode, "status": "switched"}


@app.post("/webhook")
async def webhook(signal: WebhookSignal):
    logger.info("Received signal: %s %s @ %.4f", signal.action, signal.symbol, signal.price)

    trades = _load_trades()
    mode = _get_mode()
    client = _get_client()

    # ── BUY ──────────────────────────────────────────────────────────────────
    if signal.action == "buy":
        # Validate
        if not signal.symbol:
            raise HTTPException(400, "symbol required")
        if signal.price <= 0:
            raise HTTPException(400, "price must be above 0")
        if signal.stop is None or signal.stop >= signal.price:
            raise HTTPException(400, "stop must be below entry price")

        # Check duplicate
        open_pos = _open_positions(trades)
        if signal.symbol in open_pos:
            logger.info("Position already open for %s — ignoring", signal.symbol)
            return {"status": "ignored", "reason": "position already open"}

        # Risk check
        daily_pnl = _daily_pnl(trades)
        today_count = len(_trades_today(trades))

        try:
            account = await client.get_account()
            portfolio_value = float(account.get("total", account.get("cash", 0)))
        except Exception as e:
            logger.error("Account fetch failed: %s", e)
            raise HTTPException(502, f"Cannot fetch account: {e}")

        can, reason = risk.can_trade(today_count, daily_pnl, portfolio_value)
        if not can:
            logger.warning("Trade blocked: %s", reason)
            return {"status": "blocked", "reason": reason}

        qty = risk.calculate_position_size(portfolio_value, signal.price, signal.stop)
        if qty <= 0:
            return {"status": "blocked", "reason": "Position size calculated as 0"}

        # Place limit buy
        try:
            order = await client.place_limit_order(signal.symbol, qty, signal.price)
            order_id = order.get("id", "unknown")
        except Exception as e:
            logger.error("Limit order failed: %s", e)
            raise HTTPException(502, f"Order placement failed: {e}")

        # Place stop loss
        stop_order_id = None
        try:
            stop_order = await client.place_stop_order(signal.symbol, qty, signal.stop)
            stop_order_id = stop_order.get("id", "unknown")
        except Exception as e:
            logger.warning("Stop order failed (non-fatal): %s", e)

        # Record trade
        trade = {
            "id": f"{signal.symbol}_{int(datetime.utcnow().timestamp())}",
            "symbol": signal.symbol,
            "status": "open",
            "mode": mode,
            "entry_price": signal.price,
            "stop_price": signal.stop,
            "target_price": signal.target,
            "quantity": qty,
            "order_id": order_id,
            "stop_order_id": stop_order_id,
            "confidence": signal.confidence,
            "reason": signal.reason,
            "session": signal.session,
            "change_pct": signal.change_pct,
            "relative_volume": signal.relative_volume,
            "open_time": datetime.utcnow().isoformat(),
            "pnl": 0,
        }
        trades.append(trade)
        _save_trades(trades)

        # Notify
        try:
            await notify_entry(trade)
        except Exception as e:
            logger.warning("Notify entry failed: %s", e)

        logger.info("BUY placed: %s x%d @ %.4f stop=%.4f", signal.symbol, qty, signal.price, signal.stop)
        return {
            "status": "placed",
            "symbol": signal.symbol,
            "quantity": qty,
            "entry": signal.price,
            "stop": signal.stop,
            "target": signal.target,
            "order_id": order_id,
        }

    # ── CLOSE ─────────────────────────────────────────────────────────────────
    elif signal.action == "close":
        open_pos = _open_positions(trades)
        if signal.symbol not in open_pos:
            return {"status": "ignored", "reason": "no open position for symbol"}

        try:
            result = await client.close_position(signal.symbol)
        except Exception as e:
            logger.error("Close position failed: %s", e)
            raise HTTPException(502, f"Close position failed: {e}")

        # Update trade record
        for t in reversed(trades):
            if t.get("symbol") == signal.symbol and t.get("status") == "open":
                t["status"] = "closed"
                t["exit_price"] = signal.price
                t["exit_reason"] = signal.reason or "Signal"
                t["close_time"] = datetime.utcnow().isoformat()
                entry_time = datetime.fromisoformat(t["open_time"])
                duration = str(datetime.utcnow() - entry_time).split(".")[0]
                t["duration"] = duration
                t["pnl"] = round((signal.price - t["entry_price"]) * t["quantity"], 2)
                await notify_exit(t)
                break

        _save_trades(trades)
        logger.info("CLOSED: %s @ %.4f", signal.symbol, signal.price)
        return {"status": "closed", "symbol": signal.symbol, "exit_price": signal.price}

    else:
        raise HTTPException(400, f"Unknown action: {signal.action}")


# ── daily summary scheduler ───────────────────────────────────────────────────

from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

scheduler = AsyncIOScheduler()


@scheduler.scheduled_job("cron", hour=1, minute=0, timezone=pytz.timezone("Europe/London"))
async def daily_summary_job():
    trades = _load_trades()
    today_str = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d")
    today_trades = [t for t in trades if t.get("open_time", "").startswith(today_str)]
    closed = [t for t in today_trades if t.get("status") in ("closed", "stopped")]
    wins = [t for t in closed if t.get("pnl", 0) > 0]
    losses = [t for t in closed if t.get("pnl", 0) <= 0]
    total_pnl = sum(t.get("pnl", 0) for t in closed)
    all_closed = [t for t in trades if t.get("status") in ("closed", "stopped")]
    recovered = sum(t.get("pnl", 0) for t in all_closed)
    best = max(closed, key=lambda x: x.get("pnl", 0), default={})
    worst = min(closed, key=lambda x: x.get("pnl", 0), default={})

    summary = {
        "date": today_str,
        "total_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "total_pnl": round(total_pnl, 2),
        "best_trade": {"symbol": best.get("symbol", "—"), "pnl": best.get("pnl", 0)},
        "worst_trade": {"symbol": worst.get("symbol", "—"), "pnl": worst.get("pnl", 0)},
        "recovered_so_far": round(recovered, 2),
    }
    await notify_daily_summary(summary)


@app.on_event("startup")
async def startup():
    scheduler.start()
    logger.info("PulseWebhook started in %s mode", _get_mode())


@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown()
