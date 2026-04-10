import asyncio
import os
import logging
from datetime import datetime
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = os.getenv("GMAIL_USER", "coinin350@gmail.com")
SMTP_PASS = os.getenv("GMAIL_APP_PASSWORD", "")
NOTIFY_EMAIL = os.getenv("NOTIFICATION_EMAIL", "coinin350@gmail.com")
RECOVERY_TARGET = 13611.0


async def _send(subject: str, body: str):
    if not SMTP_PASS:
        logger.warning("GMAIL_APP_PASSWORD not set — email not sent: %s", subject)
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = NOTIFY_EMAIL
    msg.attach(MIMEText(body, "plain"))
    try:
        await aiosmtplib.send(
            msg,
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            start_tls=True,
            username=SMTP_USER,
            password=SMTP_PASS,
        )
        logger.info("Email sent: %s", subject)
    except Exception as e:
        logger.error("Failed to send email: %s", e)


async def notify_entry(trade: dict):
    symbol = trade.get("symbol", "?")
    entry = trade.get("entry_price", 0)
    stop = trade.get("stop_price", 0)
    target = trade.get("target_price", 0)
    qty = trade.get("quantity", 0)
    cost = entry * qty
    risk = abs(entry - stop) * qty
    confidence = trade.get("confidence", 0)
    reason = trade.get("reason", "")
    session = trade.get("session", "")
    change_pct = trade.get("change_pct", 0)
    rel_vol = trade.get("relative_volume", 0)

    body = f"""
PulseWebhook — TRADE ENTRY
===========================
Symbol    : {symbol}
Session   : {session}
Entry     : £{entry:.4f}
Stop      : £{stop:.4f}
Target    : £{target:.4f}
Quantity  : {qty} shares
Cost      : £{cost:.2f}
Risk      : £{risk:.2f}
Confidence: {confidence}/6

Stock Activity
--------------
Change    : +{change_pct:.1f}%
Rel Volume: {rel_vol:.1f}x

Reason
------
{reason}

Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
""".strip()

    await _send(f"PulseWebhook - BUY SIGNAL: {symbol}", body)


async def notify_exit(trade: dict):
    symbol = trade.get("symbol", "?")
    entry = trade.get("entry_price", 0)
    exit_price = trade.get("exit_price", 0)
    qty = trade.get("quantity", 0)
    pnl = (exit_price - entry) * qty
    pnl_pct = ((exit_price - entry) / entry * 100) if entry else 0
    duration = trade.get("duration", "?")
    reason = trade.get("exit_reason", "Signal")
    sign = "PROFIT" if pnl >= 0 else "LOSS"

    body = f"""
PulseWebhook — TRADE CLOSED
============================
Symbol    : {symbol}
Entry     : £{entry:.4f}
Exit      : £{exit_price:.4f}
Quantity  : {qty} shares
P&L       : £{pnl:+.2f} ({pnl_pct:+.2f}%)
Result    : {sign}
Duration  : {duration}
Reason    : {reason}

Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
""".strip()

    label = f"+£{pnl:.2f}" if pnl >= 0 else f"-£{abs(pnl):.2f}"
    await _send(f"PulseWebhook - CLOSED: {symbol} {label}", body)


async def notify_daily_summary(summary: dict):
    date_str = summary.get("date", datetime.utcnow().strftime("%Y-%m-%d"))
    total = summary.get("total_trades", 0)
    wins = summary.get("wins", 0)
    losses = summary.get("losses", 0)
    win_rate = (wins / total * 100) if total else 0
    pnl = summary.get("total_pnl", 0)
    best = summary.get("best_trade", {})
    worst = summary.get("worst_trade", {})
    recovered = summary.get("recovered_so_far", 0)
    remaining = max(0, RECOVERY_TARGET - recovered)

    body = f"""
PulseWebhook — Daily Report {date_str}
=======================================
Trades    : {total}
Wins      : {wins}
Losses    : {losses}
Win Rate  : {win_rate:.1f}%
Total P&L : £{pnl:+.2f}

Best Trade : {best.get('symbol','—')} £{best.get('pnl',0):+.2f}
Worst Trade: {worst.get('symbol','—')} £{worst.get('pnl',0):+.2f}

Recovery Progress
-----------------
Target    : £{RECOVERY_TARGET:,.2f}
Recovered : £{recovered:,.2f}
Remaining : £{remaining:,.2f}

Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
""".strip()

    await _send(f"PulseWebhook - Daily Report {date_str}", body)
