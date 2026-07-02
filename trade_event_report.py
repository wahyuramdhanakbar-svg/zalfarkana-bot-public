#!/usr/bin/env python3
"""Emit trade event notification to Telegram."""
import argparse, json, base64, os
from urllib.request import Request, urlopen

B64_FILE = "/tmp/token.b64"
CHAT_ID = "YOUR_CHAT_ID"
TRADE_CSV = "/root/zalfarkana/logs/zalfarkana_trades.csv"
MODAL_AWAL = 10_000_000


def get_token():
    if os.path.exists(B64_FILE):
        with open(B64_FILE) as f:
            raw = f.read().strip()
            try:
                decoded = base64.b64decode(raw).decode().strip()
                if decoded:
                    return decoded
            except Exception:
                pass
    monitor_path = "/root/zalfarkana/.telegram_token"
    if os.path.exists(monitor_path):
        with open(monitor_path) as f:
            token = f.read().strip()
            if token:
                with open(B64_FILE, "w") as fw:
                    fw.write(base64.b64encode(token.encode()).decode())
                return token
    return ""


def send_tg(text):
    token = get_token()
    if not token:
        print("No token available")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}).encode()
    try:
        req = Request(url, data=payload, headers={"Content-Type": "application/json"})
        resp = urlopen(req, timeout=10)
        return resp.status == 200
    except Exception as e:
        print(f"TG error: {e}")
        return False


def fmt_pnl(val):
    if val is None:
        return "N/A"
    v = float(val)
    if v > 0:
        return f"+{v:,.0f} 🟢"
    elif v < 0:
        return f"{v:,.0f} 🔴"
    return f"{v:,.0f} ⚪"


def read_zf_equity():
    """Hitung equity real-time dari CSV — gak pake cache basi!"""
    try:
        import csv
        total_pl = 0
        with open(TRADE_CSV, newline="") as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) >= 9 and row[7]:
                    try:
                        total_pl += int(float(row[7]))
                    except ValueError:
                        pass
        eq = MODAL_AWAL + total_pl
        return f"{eq:,}".replace(",", ".")
    except Exception as e:
        return "?"


parser = argparse.ArgumentParser()
parser.add_argument("--symbol", required=True)
parser.add_argument("--side", required=True)
parser.add_argument("--entry", required=True)
parser.add_argument("--current", required=True)
parser.add_argument("--pnl", default="0")
parser.add_argument("--pnl-pct", default="0")
parser.add_argument("--pnl-24h", default="0")
parser.add_argument("--status", required=True)
parser.add_argument("--qty-rp", default="0")
parser.add_argument("--exit-price")
args = parser.parse_args()

status_emoji = "🟢" if args.status == "OPEN" else "🔴"
side_emoji = "🟢" if args.side == "BUY" else "🔴"
pnl_str = fmt_pnl(args.pnl)
pnl24h_str = fmt_pnl(args.pnl_24h)
entry_str = f"{float(args.entry):,.2f}"
current_str = f"{float(args.current):,.2f}"
exit_str = f"`{float(args.exit_price):,.2f}`" if args.exit_price else "`-`"

msg = (
    f"📊 **ZALFARKANA TRADE**\n"
    f"`symbol` : `{args.symbol}`\n"
    f"`side` : {side_emoji} `{args.side}`\n"
    f"`entry` : `{entry_str}`\n"
    f"`current` : `{current_str}`\n"
    f"`exit` : {exit_str}\n"
    f"`pnl` : {pnl_str}\n"
    f"`equity` : `Rp {read_zf_equity()}`\n"
    f"`qty` : `{float(args.qty_rp):,.4f}`\n"
    f"`pnl_24h` : {pnl24h_str}\n"
    f"`status` : `{args.status}` {status_emoji}"
)

if send_tg(msg):
    print(f"✅ Trade event notified: {args.symbol} {args.side}")
else:
    print(f"⚠️ Failed to notify: {args.symbol} {args.side}")