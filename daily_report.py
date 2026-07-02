#!/usr/bin/env python3
"""
Zalfarkana Daily Trade Report
Kirim laporan harian ke Telegram jam 06:00 WIB (GMT+7)
"""

import csv
import os
import requests
from datetime import datetime, timedelta, timezone

# Config
LOG_FILE = "/root/zalfarkana/logs/zalfarkana_trades.csv"
TOKEN_FILE = "/root/zalfarkana/.telegram_token"
CHAT_ID_FILE = "/root/zalfarkana/.telegram_chat_id"
CHAT_ID = os.environ.get("ZALFARKANA_CHAT_ID", "").strip()

with open(TOKEN_FILE, encoding="utf-8") as f:
    BOT_TOKEN = f.read().strip()

if not CHAT_ID:
    try:
        with open(CHAT_ID_FILE, encoding="utf-8") as f:
            CHAT_ID = f.read().strip()
    except FileNotFoundError:
        CHAT_ID = "YOUR_CHAT_ID"

# Timezone WIB = UTC+7
WIB = timezone(timedelta(hours=7))


def _parse_trade_rows():
    """Parse CSV trade file yang bisa berganti antara header/no-header."""
    with open(LOG_FILE, newline="") as f:
        rows = list(csv.reader(f))

    if not rows:
        return []

    first = [c.strip().lower() for c in rows[0]]
    has_header = any(c in first for c in ("waktu_buka", "waktu_tutup", "pair", "symbol", "entry", "pnl"))

    if has_header:
        data = rows[1:]
    else:
        data = rows

    parsed = []
    for row in data:
        if not row:
            continue
        # Normalize spacing / CRLF junk
        parsed.append([c.strip() for c in row])
    return parsed


def load_trades_24h():
    """Load trade yang tutup dalam 24 jam terakhir."""
    now = datetime.now(WIB)
    since = now - timedelta(hours=24)

    trades = []
    floating = []

    for row in _parse_trade_rows():
        # Butuh minimal kolom utama trade.
        if len(row) < 8:
            continue
        try:
            waktu_tutup = row[1] if len(row) > 1 else ""
            if not waktu_tutup:
                floating.append(row)
                continue
            dt_tutup = datetime.strptime(waktu_tutup, "%Y-%m-%d %H:%M:%S").replace(tzinfo=WIB)
            if dt_tutup >= since:
                trades.append(row)
        except Exception:
            continue

    return trades, floating


def format_pl(pl_idr):
    """Format P&L IDR dengan tanda + / -"""
    try:
        val = int(float(pl_idr))
        if val >= 0:
            return f"+Rp {val:,}".replace(",", ".")
        else:
            return f"-Rp {abs(val):,}".replace(",", ".")
    except:
        return pl_idr


def _safe_float(value, default=None):
    try:
        if value is None:
            return default
        text = str(value).strip()
        if text in ("", "-"):
            return default
        return float(text)
    except Exception:
        return default


def _fmt_num(value, decimals=2, signed=False):
    if value is None:
        return "-"
    fmt = f"{{:{'+' if signed else ''}.{decimals}f}}"
    return fmt.format(float(value))


def _field(row, idx, default=""):
    try:
        return row[idx].strip()
    except Exception:
        return default


def _is_header_row(row):
    first = [c.strip().lower() for c in row[:5]]
    return any(c in first for c in ("waktu_buka", "waktu_tutup", "pair", "symbol", "entry", "current", "pnl"))


def _get_last_trade(trades, floating):
    candidates = []
    for row in trades:
        buka = _field(row, 0)
        ts = None
        try:
            ts = datetime.strptime(buka, "%Y-%m-%d %H:%M:%S").replace(tzinfo=WIB) if buka else None
        except Exception:
            ts = None
        candidates.append((ts or datetime.min.replace(tzinfo=WIB), row, False))
    for row in floating:
        buka = _field(row, 0)
        ts = None
        try:
            ts = datetime.strptime(buka, "%Y-%m-%d %H:%M:%S").replace(tzinfo=WIB) if buka else None
        except Exception:
            ts = None
        candidates.append((ts or datetime.min.replace(tzinfo=WIB), row, True))

    if not candidates:
        return None, False
    candidates.sort(key=lambda x: x[0], reverse=True)
    _, row, is_open = candidates[0]
    return row, is_open


def _calc_24h_pnl(trades):
    total = 0.0
    for row in trades:
        total += _safe_float(_field(row, 7), 0.0) or 0.0
    return total


def build_report(trades, floating):
    """Bangun teks laporan last trade sesuai format zalfarkana."""
    now = datetime.now(WIB)
    last_trade, is_open = _get_last_trade(trades, floating)
    pnl_24h = _calc_24h_pnl(trades)

    if last_trade:
        # Header-safe access: kalau baris header nyasar, isi akan tetap aman.
        if _is_header_row(last_trade):
            pair = side = "-"
            entry = current = qty = None
            status = "CLOSE"
            pnl = pnl_pct = 0.0
            exit_text = "-"
            time_text = now.strftime("%Y-%m-%d %H:%M:%S GMT+2")
            pnl_24h = 0.0
        else:
            pair = _field(last_trade, 2, "?")
            entry = _safe_float(_field(last_trade, 3), None)
            current = _safe_float(_field(last_trade, 4), None)
            qty = _safe_float(_field(last_trade, 5), None)
            pnl_pct = _safe_float(_field(last_trade, 6), None)
            pnl = _safe_float(_field(last_trade, 7), 0.0)
            side = "BUY" if (current is None or entry is None or current >= entry) else "SELL"
            status = "OPEN" if _field(last_trade, 10, "False").lower() == "true" else "CLOSE"
            exit_price = _safe_float(_field(last_trade, 4), None)
            exit_text = "-" if is_open else (_fmt_num(exit_price) if exit_price is not None else "-")
            if current is None:
                current = entry
            time_text = _field(last_trade, 0, now.strftime("%Y-%m-%d %H:%M:%S"))
            try:
                dt = datetime.strptime(time_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=WIB)
                time_text = dt.strftime("%Y-%m-%d %H:%M:%S GMT+2")
            except Exception:
                pass
            if pnl_pct is None and entry not in (None, 0):
                pnl_pct = (pnl / entry) * 100 if abs(entry) > 0 else None
    else:
        pair = side = "-"
        entry = current = qty = None
        status = "CLOSE"
        pnl = pnl_pct = 0.0
        exit_text = "-"
        time_text = now.strftime("%Y-%m-%d %H:%M:%S GMT+2")
        pnl_24h = 0.0

    lines = [
        "[ZALFARKANA REPORT]",
        f"time: {time_text}",
        f"symbol: {pair}",
        f"side: {side}",
        f"entry: {_fmt_num(entry) if entry is not None else '-'}",
        f"current: {_fmt_num(current) if current is not None else '-'}",
        f"exit: {exit_text}",
        f"pnl: {_fmt_num(pnl, signed=True)}",
        f"pnl_pct: {_fmt_num(pnl_pct, signed=True)}%",
        f"Last 24 hour PNL: {_fmt_num(pnl_24h, signed=True)}",
        f"status: {status}",
        f"qty_rp: {_fmt_num(qty) if qty is not None else '-'}",
    ]

    return "\n".join(lines)


def send_telegram(text):
    """Kirim pesan ke Telegram."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()


def main():
    trades, floating = load_trades_24h()
    report = build_report(trades, floating)
    print(report)
    print("\n--- Sending to Telegram... ---")
    result = send_telegram(report)
    print("Sent:", result.get("ok"))

    # Panggil auto_analyzer setelah laporan harian terkirim
    print("\n--- Running auto_analyzer... ---")
    try:
        import subprocess
        here = os.path.dirname(os.path.abspath(__file__))
        subprocess.Popen(
            ["python3", os.path.join(here, "auto_analyzer.py")],
            start_new_session=True
        )
        print("auto_analyzer.py dipanggil (background)")
    except Exception as e:
        print(f"auto_analyzer gagal dipanggil: {e}")


if __name__ == "__main__":
    main()
