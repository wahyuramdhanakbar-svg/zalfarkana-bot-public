# -*- coding: utf-8 -*-
"""
============================================================
 AUTO ANALYZER v1.0 — Zalfarkana Trade Analyzer
============================================================
 Dipanggil otomatis setelah daily_report.py selesai.
 
 Yang dilakukan:
   1. Baca zalfarkana_trades.csv
   2. Hitung statistik lengkap (WR, P&L, per-pair, per-jam)
   3. Deteksi pair buruk & generate blacklist baru otomatis
   4. Update BLACKLISTED_PAIRS di zalfarkana.py langsung
   5. Kirim ringkasan + 50 trade terakhir ke Telegram (untuk dianalisa Jennifer (Hermes))
   
 Output ke Telegram:
   - Pesan 1: Ringkasan statistik + blacklist baru (~500 token)
   - Pesan 2: 50 trade terakhir CSV format (~2k token)
   
 Total token ke Kimi: ~2.500 (hemat 83% vs baca CSV langsung ~15k)
============================================================
"""

import csv
import json
import os
import re
import sys
import requests
from collections import defaultdict
from datetime import datetime, timedelta

# ── Config ───────────────────────────────────────────────
HERE       = os.path.dirname(os.path.abspath(__file__))
TRADE_LOG  = os.path.join(HERE, "logs", "zalfarkana_trades.csv")
BOT_FILE   = os.path.join(HERE, "zalfarkana.py")
ENV_FILE   = os.path.join(HERE, ".env")
REPORT_LOG = os.path.join(HERE, "logs", "auto_analyzer.log")

TOKEN_FILE = os.path.join(HERE, ".telegram_token")
CHAT_ID_FILE = os.path.join(HERE, ".telegram_chat_id")
CHAT_ID = os.environ.get("ZALFARKANA_CHAT_ID", "").strip()

with open(TOKEN_FILE, encoding="utf-8") as f:
    TELEGRAM_TOKEN = f.read().strip()

if not CHAT_ID:
    try:
        with open(CHAT_ID_FILE, encoding="utf-8") as f:
            CHAT_ID = f.read().strip()
    except FileNotFoundError:
        CHAT_ID = "YOUR_CHAT_ID"

# Threshold blacklist otomatis — Jennifer kalibrasi 2026-06-20
# Data 452 trade: pair buruk konsisten WR<35% + P&L < -30k
BL_MIN_TRADES = 5       # minimal trade sebelum dinilai
BL_MAX_WR     = 35      # WR < 35% → kandidat blacklist (dinaikkan dari 20%)
BL_MAX_PL     = -30000  # P&L < -30k IDR → kandidat blacklist (diperketat dari -20k)

# ── Helper ───────────────────────────────────────────────
def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(REPORT_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def get_token():
    return TELEGRAM_TOKEN

def send_telegram(token, chat_id, text):
    """Kirim pesan Telegram, auto-split kalau >4000 char."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        r = requests.post(url, json={
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML"
        }, timeout=15)
        r.raise_for_status()

def safe_float(v, default=0.0):
    try: return float(v) if v else default
    except: return default

def safe_int(v, default=0):
    try: return int(float(v)) if v else default
    except: return default

# ── Load data ────────────────────────────────────────────
def load_trades():
    rows = []
    if not os.path.exists(TRADE_LOG):
        return rows
    with open(TRADE_LOG, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if r.get("alasan") in ("TP", "SL", "TIME"):
                rows.append(r)
    return rows

# ── Statistik ────────────────────────────────────────────
def compute_stats(rows):
    """Hitung statistik lengkap dari semua trade."""
    total = len(rows)
    if total == 0:
        return None

    tp_n  = sum(1 for r in rows if r["alasan"] == "TP")
    sl_n  = sum(1 for r in rows if r["alasan"] == "SL")
    tm_n  = sum(1 for r in rows if r["alasan"] == "TIME")
    wr    = tp_n / total * 100

    pl_total = sum(safe_int(r["pl_idr"]) for r in rows)
    pl_tp    = sum(safe_int(r["pl_idr"]) for r in rows if r["alasan"] == "TP")
    pl_sl    = sum(safe_int(r["pl_idr"]) for r in rows if r["alasan"] == "SL")

    avg_tp = pl_tp / tp_n if tp_n else 0
    avg_sl = pl_sl / sl_n if sl_n else 0
    rr     = abs(avg_tp / avg_sl) if avg_sl else 0

    # Per-pair
    pair_stats = defaultdict(lambda: {"tp": 0, "sl": 0, "pl": 0, "n": 0})
    for r in rows:
        p = r["pair"]
        pair_stats[p]["n"]  += 1
        pair_stats[p]["pl"] += safe_int(r["pl_idr"])
        if r["alasan"] == "TP": pair_stats[p]["tp"] += 1
        elif r["alasan"] == "SL": pair_stats[p]["sl"] += 1

    # Per-jam
    hour_stats = defaultdict(lambda: {"tp": 0, "sl": 0, "n": 0})
    for r in rows:
        try:
            h = datetime.strptime(r["waktu_buka"], "%Y-%m-%d %H:%M:%S").hour
            hour_stats[h]["n"] += 1
            if r["alasan"] == "TP": hour_stats[h]["tp"] += 1
            elif r["alasan"] == "SL": hour_stats[h]["sl"] += 1
        except: pass

    # 7 hari terakhir
    week_ago = datetime.now() - timedelta(days=7)
    rows_7d  = []
    for r in rows:
        try:
            if datetime.strptime(r["waktu_buka"], "%Y-%m-%d %H:%M:%S") >= week_ago:
                rows_7d.append(r)
        except: pass
    tp_7d = sum(1 for r in rows_7d if r["alasan"] == "TP")
    wr_7d = tp_7d / len(rows_7d) * 100 if rows_7d else 0
    pl_7d = sum(safe_int(r["pl_idr"]) for r in rows_7d)

    return {
        "total": total, "tp": tp_n, "sl": sl_n, "time": tm_n,
        "wr": wr, "pl_total": pl_total, "pl_tp": pl_tp, "pl_sl": pl_sl,
        "avg_tp": avg_tp, "avg_sl": avg_sl, "rr": rr,
        "pair_stats": dict(pair_stats),
        "hour_stats": dict(hour_stats),
        "rows_7d": len(rows_7d), "wr_7d": wr_7d, "pl_7d": pl_7d,
    }

# ── Deteksi blacklist ─────────────────────────────────────
def detect_blacklist(pair_stats):
    """
    Deteksi pair buruk berdasarkan data historis.
    Return: (blacklist_baru: set, alasan: dict)
    """
    new_bl   = set()
    reasons  = {}

    for pair, s in pair_stats.items():
        n  = s["n"]
        pl = s["pl"]
        wr = s["tp"] / n * 100 if n > 0 else 0

        if n < BL_MIN_TRADES:
            continue  # data terlalu sedikit, skip

        if wr < BL_MAX_WR and pl < BL_MAX_PL:
            new_bl.add(pair)
            reasons[pair] = f"WR {wr:.0f}%, P&L Rp {pl:,}".replace(",", ".")

    return new_bl, reasons

# ── Update blacklist di zalfarkana.py ────────────────────
def update_blacklist_in_code(new_blacklist: set):
    """
    Ganti BLACKLISTED_PAIRS di zalfarkana.py dengan blacklist baru.
    Blacklist lama dihapus seluruhnya, diganti yang baru dari data.
    """
    if not os.path.exists(BOT_FILE):
        log(f"⚠️  zalfarkana.py tidak ditemukan di {BOT_FILE}")
        return False

    with open(BOT_FILE, encoding="utf-8") as f:
        content = f.read()

    # Format set sebagai string multi-line
    if new_blacklist:
        pairs_str = ",\n    ".join(f'"{p}"' for p in sorted(new_blacklist))
        new_block = (
            f"BLACKLISTED_PAIRS = {{\n"
            f"    # Auto-generated oleh auto_analyzer.py — {datetime.now():%Y-%m-%d}\n"
            f"    # Pair dengan WR <{BL_MAX_WR}% DAN P&L < Rp {BL_MAX_PL:,} (min {BL_MIN_TRADES} trade)\n"
            f"    {pairs_str},\n"
            f"}}"
        ).replace(",", ".")
        # Koreksi: koma di pairs_str harus tetap koma, bukan titik
        new_block = (
            f"BLACKLISTED_PAIRS = {{\n"
            f"    # Auto-generated oleh auto_analyzer.py — {datetime.now():%Y-%m-%d}\n"
            f"    # Pair dengan WR <{BL_MAX_WR}% DAN P&L < Rp {abs(BL_MAX_PL):,} (min {BL_MIN_TRADES} trade)\n"
            f"    {pairs_str},\n"
            f"}}"
        )
    else:
        new_block = (
            f"BLACKLISTED_PAIRS = set()  "
            f"# Auto-cleared oleh auto_analyzer.py — {datetime.now():%Y-%m-%d}"
        )

    # Ganti blok BLACKLISTED_PAIRS (single-line atau multi-line)
    pattern = r"BLACKLISTED_PAIRS\s*=\s*\{[^}]*\}|BLACKLISTED_PAIRS\s*=\s*set\(\)"
    new_content, n_subs = re.subn(pattern, new_block, content, flags=re.DOTALL)

    if n_subs == 0:
        log("⚠️  Tidak menemukan BLACKLISTED_PAIRS di zalfarkana.py")
        return False

    # Tulis ulang file
    with open(BOT_FILE, "w", encoding="utf-8") as f:
        f.write(new_content)

    log(f"✅ BLACKLISTED_PAIRS diupdate: {len(new_blacklist)} pair")
    return True

# ── Update GOLDEN_HOURS di zalfarkana.py ─────────────────
def update_golden_hours_in_code(hour_stats: dict):
    """
    Hitung ulang jam terbaik dari data, update GOLDEN_HOURS_UTC di zalfarkana.py.
    Kriteria: jam dengan WR > 40% DAN minimal 5 trade.
    """
    if not os.path.exists(BOT_FILE):
        return False

    golden = set()
    for h, s in hour_stats.items():
        n  = s["n"]
        wr = s["tp"] / n * 100 if n > 0 else 0
        if n >= 5 and wr >= 40:
            golden.add(h)

    if not golden:
        log("⚠️  Tidak ada jam dengan WR ≥40% + min 5 trade, golden hours tidak diubah")
        return False

    hours_str = ", ".join(str(h) for h in sorted(golden))
    new_line  = f"GOLDEN_HOURS_UTC = {{{hours_str}}}  # Auto-updated {datetime.now():%Y-%m-%d}"

    with open(BOT_FILE, encoding="utf-8") as f:
        content = f.read()

    pattern     = r"GOLDEN_HOURS_UTC\s*=\s*\{[^}]*\}[^\n]*"
    new_content, n_subs = re.subn(pattern, new_line, content)

    if n_subs == 0:
        log("⚠️  GOLDEN_HOURS_UTC tidak ditemukan di zalfarkana.py")
        return False

    with open(BOT_FILE, "w", encoding="utf-8") as f:
        f.write(new_content)

    log(f"✅ GOLDEN_HOURS_UTC diupdate: {{{hours_str}}}")
    return True

# ── Format pesan Telegram ─────────────────────────────────
def format_summary_msg(stats, new_bl, bl_reasons, golden_updated):
    s    = stats
    date = datetime.now().strftime("%d-%b-%Y")

    # Tren 7 hari
    trend_emoji = "📈" if s["pl_7d"] >= 0 else "📉"
    trend_sign  = "+" if s["pl_7d"] >= 0 else ""

    # Top 5 pair terbaik & terburuk
    pair_list = [
        (pair, d["tp"] / d["n"] * 100 if d["n"] else 0, d["pl"], d["n"])
        for pair, d in s["pair_stats"].items()
        if d["n"] >= 3
    ]
    best_pairs  = sorted(pair_list, key=lambda x: x[1], reverse=True)[:5]
    worst_pairs = sorted(pair_list, key=lambda x: x[2])[:5]

    # Jam terbaik & terburuk
    hour_list = [
        (h, d["tp"] / d["n"] * 100 if d["n"] else 0, d["n"])
        for h, d in s["hour_stats"].items()
        if d["n"] >= 5
    ]
    best_hours  = sorted(hour_list, key=lambda x: x[1], reverse=True)[:5]
    worst_hours = sorted(hour_list, key=lambda x: x[1])[:3]

    msg = f"""🤖 *AUTO ANALYZER — {date}*
━━━━━━━━━━━━━━━━━━━━━━

📊 *STATISTIK KESELURUHAN*
Total trade : {s['total']}
TP / SL / TIME : {s['tp']} / {s['sl']} / {s['time']}
Win Rate : {s['wr']:.1f}%
P&L Total : Rp {s['pl_total']:,}
Avg TP : Rp {s['avg_tp']:,.0f}
Avg SL : Rp {s['avg_sl']:,.0f}
RR Ratio : {s['rr']:.2f}

{trend_emoji} *7 HARI TERAKHIR*
Trade : {s['rows_7d']} | WR : {s['wr_7d']:.1f}%
P&L : {trend_sign}Rp {s['pl_7d']:,}

🏆 *TOP 5 PAIR TERBAIK*
""".replace(",", ".")

    for pair, wr, pl, n in best_pairs:
        sign = "+" if pl >= 0 else ""
        msg += f"  {pair}: WR {wr:.0f}% | {sign}Rp {pl:,} ({n} trade)\n".replace(",", ".")

    msg += "\n💀 *TOP 5 PAIR TERBURUK*\n"
    for pair, wr, pl, n in worst_pairs:
        msg += f"  {pair}: WR {wr:.0f}% | Rp {pl:,} ({n} trade)\n".replace(",", ".")

    msg += "\n⏰ *JAM TERBAIK (UTC)*\n"
    for h, wr, n in best_hours:
        msg += f"  {h:02d}:00 — WR {wr:.0f}% ({n} trade)\n"

    msg += "\n🚫 *JAM TERBURUK (UTC)*\n"
    for h, wr, n in worst_hours:
        msg += f"  {h:02d}:00 — WR {wr:.0f}% ({n} trade)\n"

    if new_bl:
        msg += f"\n🔴 *BLACKLIST BARU AKTIF* ({len(new_bl)} pair)\n"
        for pair in sorted(new_bl):
            reason = bl_reasons.get(pair, "")
            msg += f"  ✗ {pair} — {reason}\n"
        msg += "_Blacklist lama dihapus, diganti data terbaru_\n"
    else:
        msg += "\n✅ *Tidak ada pair baru yang diblacklist*\n"

    if golden_updated:
        hours_str = ", ".join(
            f"{h:02d}:00"
            for h in sorted(
                h for h, d in s["hour_stats"].items()
                if d["n"] >= 5 and d["tp"] / d["n"] * 100 >= 40
            )
        )
        msg += f"\n🕐 *Golden Hours diupdate*: {hours_str} UTC\n"

    msg += "\n_Data dikirim ke Jennifer untuk dianalisa..._"
    return msg

def format_raw_trades_msg(rows, n=50):
    """Format 50 trade terakhir sebagai tabel ringkas untuk dianalisa Jennifer."""
    recent = rows[-n:]
    date   = datetime.now().strftime("%d-%b-%Y")

    msg = f"📋 *{n} TRADE TERAKHIR — {date}*\n"
    msg += "_Format: waktu|pair|alasan|P&L|RSI|MACD|EMA>21|skor_\n\n"
    msg += "```\n"

    for r in recent:
        try:
            t     = datetime.strptime(r["waktu_buka"], "%Y-%m-%d %H:%M:%S").strftime("%m/%d %H:%M")
            pair  = r["pair"].replace("USDT", "")[:8].ljust(8)
            alasan = r["alasan"][:2].ljust(2)
            pl    = safe_int(r["pl_idr"])
            pl_s  = f"{'+' if pl>=0 else ''}{pl//1000}k".rjust(6)
            rsi   = f"{safe_float(r.get('rsi')):.0f}".rjust(3)
            macd  = "+" if safe_float(r.get("macd_hist")) > 0 else "-"
            ema   = "↑" if r.get("ema9_gt_21") == "True" else "↓"
            skor  = str(safe_int(r.get("skor"))).rjust(3)
            msg += f"{t} {pair} {alasan} {pl_s} RSI{rsi} M{macd} E{ema} s{skor}\n"
        except:
            continue

    msg += "```\n"
    msg += f"\n_Jennifer analisa pattern dari {n} trade ini_ 🙏"
    return msg

# ── Main ─────────────────────────────────────────────────
def main():
    log("=" * 50)
    log("AUTO ANALYZER mulai")

    token = get_token()
    if not token:
        log("❌ TELEGRAM_BOT_TOKEN tidak ditemukan")
        sys.exit(1)

    # 1. Load data
    rows = load_trades()
    if not rows:
        log("❌ Tidak ada data trade")
        sys.exit(1)
    log(f"✅ {len(rows)} trade dimuat")

    # 2. Hitung statistik
    stats = compute_stats(rows)
    log(f"✅ Statistik: WR {stats['wr']:.1f}%, P&L Rp {stats['pl_total']:,}".replace(",", "."))

    # 3. Deteksi & update blacklist
    new_bl, bl_reasons = detect_blacklist(stats["pair_stats"])
    log(f"✅ Blacklist terdeteksi: {len(new_bl)} pair → {sorted(new_bl)}")
    bl_updated = update_blacklist_in_code(new_bl)

    # 4. Update golden hours
    golden_updated = update_golden_hours_in_code(stats["hour_stats"])

    # 5. Restart bot jika ada perubahan
    if bl_updated or golden_updated:
        log("🔄 Ada perubahan kode — restart zalfarkana_vps.py...")
        import subprocess, signal
        # Kill old process
        try:
            result = subprocess.run(["pgrep", "-f", "zalfarkana_vps.py"], capture_output=True, text=True)
            for pid in result.stdout.strip().split():
                try:
                    os.kill(int(pid), signal.SIGTERM)
                    log(f"  Killed PID {pid}")
                except: pass
        except: pass

        import time
        time.sleep(3)

        # Start baru
        log_path = os.path.join(HERE, "logs", "vps_activity.log")
        proc = subprocess.Popen(
            ["python3", os.path.join(HERE, "zalfarkana_vps.py")],
            stdout=open(log_path, "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True
        )
        time.sleep(3)
        log(f"✅ Bot restart, PID baru: {proc.pid}")

    # 6. Kirim ringkasan ke Telegram
    msg1 = format_summary_msg(stats, new_bl, bl_reasons, golden_updated)
    send_telegram(token, TELEGRAM_CHAT_ID, msg1)
    log("✅ Pesan ringkasan terkirim")

    # 7. Kirim 50 trade terakhir untuk Kimi
    msg2 = format_raw_trades_msg(rows, n=50)
    send_telegram(token, TELEGRAM_CHAT_ID, msg2)
    log("✅ Pesan raw trades terkirim")

    log("AUTO ANALYZER selesai")
    log("=" * 50)

if __name__ == "__main__":
    main()
