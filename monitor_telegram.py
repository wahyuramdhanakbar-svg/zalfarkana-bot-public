#!/usr/bin/env python3
"""Zalfarkana Trade Monitor — kirim notif trade ke Telegram bot via @Monitor_Zalfarkana_Bot"""

import os, re, time, requests, threading
from datetime import datetime

TOKEN_FILE = "/root/zalfarkana/.telegram_token"
CHAT_ID_FILE = "/root/zalfarkana/.telegram_chat_id"
CHAT_ID = os.environ.get("ZALFARKANA_CHAT_ID", "YOUR_CHAT_ID")
LOG_PATH = "/root/zalfarkana/logs/vps_activity.log"

with open(TOKEN_FILE, encoding="utf-8") as f:
    BOT_TOKEN = f.read().strip()

if not CHAT_ID:
    try:
        with open(CHAT_ID_FILE, encoding="utf-8") as f:
            CHAT_ID = f.read().strip()
    except FileNotFoundError:
        CHAT_ID = "YOUR_CHAT_ID"

TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
last_send = 0
lock = threading.Lock()

REPORT_RE = re.compile(
    r"\[ZALFARKANA REPORT\]\n"
    r"time: (?P<time>.+)\n"
    r"symbol: (?P<symbol>.+)\n"
    r"side: (?P<side>.+)\n"
    r"entry: (?P<entry>.+)\n"
    r"current: (?P<current>.+)\n"
    r"exit: (?P<exit>.+)\n"
    r"pnl: (?P<pnl>.+)\n"
    r"pnl_pct: (?P<pnl_pct>.+)\n"
    r"Last 24 hour PNL: (?P<pnl_24h>.+)\n"
    r"status: (?P<status>.+)\n"
)

OPEN_RE = re.compile(r"BUY (\S+) @ ([\d.]+) \| skor (\d+) \| (.+)")
CLOSE_RE = re.compile(r"SELL (\S+) @ ([\d.]+) \((SL|TP|TIME)\) \| (P/L .+) \|")
STATUS_RE = re.compile(r"STATUS \| ekuitas ([^|]+) \| posisi terbuka (\d+) \| floating ([^|]+) \| WR (\S+) \| P/L hari ini (.+)")


def send(msg):
    global last_send
    with lock:
        now = time.time()
        if now - last_send < 1:
            time.sleep(1 - (now - last_send))
        last_send = time.time()
    r = requests.post(TELEGRAM_URL, json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    if not r.ok:
        print(f"[!] Telegram error: {r.text[:100]}")
    else:
        print(f"[>] {msg[:80]}")


def follow_log(path):
    with open(path, "r", encoding="utf-8") as f:
        f.seek(0, os.SEEK_END)
        while True:
            line = f.readline()
            if line:
                yield line.rstrip("\n")
            else:
                time.sleep(0.5)


def report_text(fields):
    emoji = "🟢" if fields.get("side") == "BUY" else "🔴"
    status_emoji = "📗" if fields.get("status") == "OPEN" else "📕"
    lines = (
        f"{emoji} <b>[ZALFARKANA REPORT]</b>\n"
        f"⏰ <code>{fields['time']}</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Pair</b>     : <code>{fields['symbol']}</code>\n"
        f"📋 <b>Side</b>     : {emoji} {fields['side']}\n"
        f"💵 <b>Entry</b>    : <code>{fields['entry']}</code>\n"
    )
    if fields.get("skor"):
        lines += f"⭐ <b>Skor</b>     : <code>{fields['skor']}</code>\n"
    if fields.get("size"):
        lines += f"💰 <b>Size</b>     : <code>{fields['size']}</code>\n"
    lines += (
        f"📊 <b>Current</b>  : <code>{fields['current']}</code>\n"
        f"🚪 <b>Exit</b>     : <code>{fields['exit']}</code>\n"
        f"📈 <b>P&L</b>      : {fields['pnl']}\n"
        f"📉 <b>P&L %</b>    : {fields['pnl_pct']}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🕐 <b>24h P&L</b>   : {fields['pnl_24h']}\n"
        f"{status_emoji} <b>Status</b>   : {fields['status']}\n"
    )
    return lines


if __name__ == "__main__":
    print("[*] Zalfarkana Monitor started")
    print(f"[*] Watching: {LOG_PATH}")
    send("🚀 <b>Zalfarkana Monitor Aktif</b>\nMemantau trade real-time...")

    for line in follow_log(LOG_PATH):
        m = REPORT_RE.match(line)
        if m:
            send(report_text(m.groupdict()))
            continue

        m = OPEN_RE.search(line)
        if m:
            pair, price, score, size = m.groups()
            send(report_text({
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S WIB"),
                "symbol": pair,
                "side": "BUY",
                "entry": price,
                "current": price,
                "exit": "➖",
                "pnl": "⚪ 0.00 (floating)",
                "pnl_pct": "⚪ 0.00%",
                "pnl_24h": "⏳ lihat laporan harian",
                "status": "OPEN",
                "skor": score,
                "size": size,
            }))
            continue

        m = CLOSE_RE.search(line)
        if m:
            pair, price, reason, pnl = m.groups()
            pnl_clean = pnl.replace("P/L ", "")
            sign = "🔴" if pnl_clean.startswith("-") else "🟢"
            emoji_reason = {"SL": "🛑", "TP": "✅", "TIME": "⏰"}.get(reason, "❓")
            send(report_text({
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S WIB"),
                "symbol": pair,
                "side": "SELL",
                "entry": price,
                "current": price,
                "exit": price,
                "pnl": f"{sign} {pnl_clean} {emoji_reason}",
                "pnl_pct": "—",
                "pnl_24h": "—",
                "status": "CLOSE",
            }))
            continue

        m = STATUS_RE.search(line)
        if m:
            ekuitas, posisi, floating, wr, pl = m.groups()
            send(
                f"📊 <b>STATUS</b>\n"
                f"├ Ekuitas: {ekuitas}\n"
                f"├ Posisi: {posisi} open\n"
                f"├ Floating: {floating}\n"
                f"├ WR: {wr}\n"
                f"└ P/L Hari Ini: {pl}"
            )
