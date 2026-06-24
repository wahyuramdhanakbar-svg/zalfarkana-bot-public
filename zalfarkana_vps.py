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
