# -*- coding: utf-8 -*-
"""
============================================================
 ZALFARKANA VPS — Runner Headless (tanpa GUI)
============================================================
 Menjalankan logika bot yang SAMA PERSIS dengan zalfarkana.py,
 tapi tanpa jendela — cocok untuk VPS Linux 24/7.

 - Membaca pengaturan dari vps_config.json (dibuat otomatis jika
   belum ada, berisi default + PAPER mode).
 - Mode AUTO saja (tidak ada manusia untuk memilih sinyal).
 - Semua aktivitas ditulis ke logs/vps_activity.log
 - Trade tetap tercatat di logs/zalfarkana_trades.csv (sama spt GUI)
 - Berhenti rapi dengan Ctrl+C / systemctl stop (SIGTERM).

 SYARAT: file zalfarkana.py harus ADA di folder yang sama.
 Dependensi: pip install requests ccxt  +  apt install python3-tk
   (python3-tk diperlukan hanya agar 'import tkinter' di zalfarkana.py
    tidak gagal; GUI tidak pernah dibuka di sini.)
============================================================
"""

import json
import os
import queue
import signal
import sys
import threading
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "vps_config.json")
ACTIVITY_LOG = os.path.join(HERE, "logs", "vps_activity.log")
SIGNAL_FILE = "/tmp/freqtrade_signal.json"
SIGNAL_POLL_SECS = 10  # polling tiap 10 detik
os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)

# Impor engine dari file utama (satu sumber kebenaran untuk strategi).
try:
    from zalfarkana import ZalfarkanaBot, DEFAULTS, MarketData
except Exception as e:
    sys.stderr.write(
        "GAGAL impor zalfarkana.py.\n"
        "Pastikan zalfarkana.py ada di folder yang sama, dan python3-tk "
        "sudah terpasang:\n  sudo apt install python3-tk\nDetail: %s\n" % e)
    sys.exit(1)


def log_line(msg):
    """Tulis ke file log + cetak ke stdout (terlihat di journalctl)."""
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(ACTIVITY_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_config():
    """Muat vps_config.json; buat default jika belum ada."""
    if not os.path.exists(CONFIG_PATH):
        cfg = dict(DEFAULTS)
        # tambahan khusus VPS
        template = {
            "_catatan": "Edit nilai di bawah. paper=true WAJIB sampai siap LIVE.",
            "paper": True,
            "api_key": "",
            "api_secret": "",
            "cp_key": "",
            "modal_idr": cfg["modal_idr"],
            "daily_loss_limit_idr": cfg["daily_loss_limit_idr"],
            "max_open": cfg["max_open"],
            "tp_pct": cfg["tp_pct"],
            "sl_pct": cfg["sl_pct"],
            "pos_size_pct": cfg["pos_size_pct"],
            "score_entry": cfg["score_entry"],
            "score_override": cfg["score_override"],
            "time_stop_h": cfg["time_stop_h"],
            "top_n_pairs": cfg["top_n_pairs"],
            "max_size_idr": cfg["max_size_idr"],
            "high_conv_enable": cfg["high_conv_enable"],
            "high_conv_score": cfg["high_conv_score"],
            "high_conv_pct": cfg["high_conv_pct"],
            "kurs_auto": cfg["kurs_auto"],
            "usdt_idr": cfg["usdt_idr"],
        }
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(template, f, indent=2)
        log_line(f"vps_config.json dibuat di {CONFIG_PATH} — edit lalu jalankan lagi.")
        return template
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def build_cfg(conf):
    cfg = dict(DEFAULTS)
    for k in cfg:
        if k in conf:
            cfg[k] = conf[k]
    return cfg


# Score default untuk sinyal eksternal dari Freqtrade
# Lebih tinggi dari score_entry (68), di bawah score_override (92)
EXTERNAL_SIGNAL_SCORE = 75


class SignalListener(threading.Thread):
    """Polling sinyal dari Freqtrade webhook → eksekusi via ZalfarkanaBot."""

    def __init__(self, bot, stop_event):
        super().__init__(daemon=True)
        self.bot = bot
        self.stop = stop_event
        self.last_trade_id = 0
        self.last_mtime = 0

    def run(self):
        log_line("📡 SignalListener: mulai polling /tmp/freqtrade_signal.json tiap 10 detik")
        while not self.stop.is_set():
            try:
                self._poll()
            except Exception as e:
                log_line(f"⚠️ SignalListener error: {e}")
            self.stop.wait(SIGNAL_POLL_SECS)

    def _poll(self):
        if not os.path.exists(SIGNAL_FILE):
            return

        mtime = os.path.getmtime(SIGNAL_FILE)
        # Skip kalo file gak berubah
        if mtime <= self.last_mtime and self.last_mtime > 0:
            return

        with open(SIGNAL_FILE) as f:
            data = json.load(f)

        trade_id = data.get("trade_id", 0)
        signal_type = data.get("signal", "")

        # Skip kalo udah diproses atau bukan sinyal buy
        if trade_id <= self.last_trade_id:
            self.last_mtime = mtime
            return
        if signal_type != "buy":
            self.last_trade_id = trade_id
            self.last_mtime = mtime
            return

        self.last_trade_id = trade_id
        self.last_mtime = mtime

        pair = data.get("pair", "").replace("/", "")
        price = data.get("price", 0)
        fq_strategy = data.get("strategy", "unknown")

        if not pair or not price:
            log_line(f"⚠️ SignalListener: data tidak lengkap — {data}")
            return

        log_line(f"📡 Signal terima: {pair} @ {price} | strategi Freqtrade: {fq_strategy}")
        self._process_signal(pair, float(price), fq_strategy)

    def _process_signal(self, pair, price, fq_strategy="unknown"):
        bot = self.bot

        # 1. Cek daily stop
        if bot.risk.daily_stop_hit():
            log_line(f"⏭️ Signal skip {pair}: daily loss limit tercapai")
            return

        # 2. Cek apakah pair udah open
        if pair in bot.positions:
            log_line(f"⏭️ Signal skip {pair}: posisi sudah terbuka")
            return

        # 3. Cek cooldown
        cooldown_until = bot.cooldown.get(pair, 0)
        if cooldown_until > time.time():
            sisa = int(cooldown_until - time.time())
            log_line(f"⏭️ Signal skip {pair}: cooldown {sisa//60}m tersisa")
            return

        # 4. Cek max open positions
        open_count = len(bot.positions)
        max_open = bot.cfg.get("max_open", 3)
        score_override = bot.cfg.get("score_override", 92)

        can_override = (EXTERNAL_SIGNAL_SCORE >= score_override
                        and not bot.risk.override_in_use)
        is_override = False

        if open_count >= max_open:
            if can_override:
                is_override = True
                log_line(f"🔥 Override slot! {pair} — masuk slot +1")
            else:
                log_line(f"⏭️ Signal skip {pair}: max open {max_open} tercapai")
                return

        # 5. Validasi buku order
        book = bot.md.book(pair)
        if not book:
            log_line(f"⏭️ Signal skip {pair}: order book tidak tersedia")
            return

        # 6. Eksekusi!
        sig = {"symbol": pair, "score": EXTERNAL_SIGNAL_SCORE, "snap": None}
        bot.open_position(sig, is_override=is_override)
        log_line(f"✅ Signal eksekusi: BUY {pair} @ {price} "
                 f"{'(OVERRIDE)' if is_override else ''} | dari Freqtrade ({fq_strategy})")
        bot.q.put(("positions", None))


class HeadlessRunner:
    def __init__(self, conf):
        self.conf = conf
        self.cfg = build_cfg(conf)
        self.q = queue.Queue()
        paper = bool(conf.get("paper", True))
        if not paper and (not conf.get("api_key") or not conf.get("api_secret")):
            log_line("Mode LIVE tapi API key kosong — dipaksa kembali ke PAPER.")
            paper = True
        self.paper = paper
        self.bot = ZalfarkanaBot(
            self.cfg, self.q, mode_auto=True, paper=paper,
            api_key=conf.get("api_key", ""), api_secret=conf.get("api_secret", ""),
            cp_key=conf.get("cp_key", ""))
        self.stop = threading.Event()
        self.listener = SignalListener(self.bot, self.stop)

    def consume(self):
        """Kuras antrian UI bot, tulis pesan teks ke file log."""
        last_heartbeat = 0
        while not self.stop.is_set():
            try:
                kind, payload = self.q.get(timeout=1)
                if kind in ("log", "win", "loss"):
                    with open(ACTIVITY_LOG, "a", encoding="utf-8") as f:
                        f.write(payload + "\n")
                    print(payload, flush=True)
                elif kind == "signals" and payload:
                    top = payload[0]
                    log_line(f"Scan: {len(payload)} sinyal | teratas "
                             f"{top['symbol']} skor {top['score']}")
            except queue.Empty:
                pass
            # heartbeat tiap 5 menit: ekuitas, posisi, win-rate
            now = time.time()
            if now - last_heartbeat >= 300:
                b = self.bot
                wr = (b.win_count / b.closed_count * 100) if b.closed_count else 0
                log_line(f"STATUS | ekuitas Rp {b.equity_idr():,.0f} | "
                         f"posisi terbuka {len(b.positions)} | "
                         f"floating Rp {b.floating_pl_idr():,.0f} | "
                         f"WR {wr:.0f}% ({b.win_count}/{b.closed_count}) | "
                         f"P/L hari ini Rp {b.risk.realized_pl_idr:,.0f}"
                         .replace(",", "."))
                last_heartbeat = now

    def start(self):
        log_line("=" * 50)
        log_line(f"ZALFARKANA VPS mulai | mode AUTO | "
                 f"{'PAPER' if self.paper else 'LIVE'} | modal Rp "
                 f"{self.cfg['modal_idr']:,.0f}".replace(",", "."))
        log_line("=" * 50)
        consumer = threading.Thread(target=self.consume, daemon=True)
        consumer.start()
        self.listener.start()
        self.bot.start()

    def shutdown(self, *_):
        log_line("Sinyal berhenti diterima — menutup bot dengan rapi...")
        self.bot.stop_flag.set()
        time.sleep(4)
        self.stop.set()
        # ringkasan akhir
        b = self.bot
        wr = (b.win_count / b.closed_count * 100) if b.closed_count else 0
        log_line(f"RINGKASAN AKHIR | ekuitas Rp {b.equity_idr():,.0f} | "
                 f"total trade {b.closed_count} | WR {wr:.0f}%".replace(",", "."))
        log_line("Bot berhenti. Sampai jumpa.")
        sys.exit(0)


def main():
    conf = load_config()
    # jika file baru dibuat, beri kesempatan user mengisi sebelum jalan live
    runner = HeadlessRunner(conf)
    signal.signal(signal.SIGINT, runner.shutdown)
    signal.signal(signal.SIGTERM, runner.shutdown)
    runner.start()
    # jaga proses utama tetap hidup
    while not runner.stop.is_set():
        time.sleep(2)


if __name__ == "__main__":
    main()
