# -*- coding: utf-8 -*-
"""
============================================================
 PROJECT ZALFARKANA v1.5
 Bot Scalping Tokocrypto — TP +2% / SL -1% (RR 2:1)
============================================================
 Mode operasi : AUTO (full otomatis)  |  SIGNAL (bot beri sinyal, Anda eksekusi)
 Mode dana    : PAPER (simulasi)      |  LIVE (uang asli, API key Tokocrypto)

 Sumber sinyal (skor 0-100):
   - Teknikal  : RSI, MACD, Bollinger Band, EMA, lonjakan volume (data candle)
   - Fundamental: CoinGecko (market cap rank, FDV/MC, perubahan 24 jam)
   - Sentimen  : Fear & Greed Index + trending CoinGecko
   - Berita    : CryptoPanic (opsional, API key gratis)

 PENTING:
   - TIDAK ADA fungsi withdraw di kode ini. Matikan juga izin withdraw
     pada API key Tokocrypto Anda.
   - Selalu jalankan PAPER dulu beberapa hari sebelum LIVE.
   - Trading kripto berisiko. Tidak ada jaminan profit.

 Dependensi: pip install requests ccxt
   (ccxt hanya wajib untuk mode LIVE; PAPER cukup requests)
============================================================
"""

import csv
import hashlib
import hmac
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.parse
from datetime import datetime, date, timedelta
from tkinter import ttk, messagebox

try:
    import requests
except ImportError:
    raise SystemExit("Modul 'requests' belum terpasang. Jalankan: pip install requests")

try:
    import ccxt  # github.com/ccxt/ccxt — dukungan resmi Tokocrypto
except ImportError:
    ccxt = None

# ============================================================
# KONFIGURASI DEFAULT (bisa diubah lewat GUI)
# ============================================================
DEFAULTS = {
    "modal_idr": 10_000_000,        # modal awal (paper) / acuan sizing
    "usdt_idr": 16_300,             # kurs estimasi utk konversi tampilan
    "tp_pct": 1.0,                  # take profit % — RR 2:1 (Jennifer: pastikan avg_tp/avg_sl >= 2.0)
    "sl_pct": 5.0,                  # stop loss %
    "fee_pct": 0.15,                # fee taker per sisi (%)
    "max_open": 8,                  # maksimum posisi terbuka
    "daily_loss_limit_idr": 300_000,# loss harian maksimum (Rp) -> auto stop
    "score_entry": 50,              # skor minimum entry — dinaikkan Jennifer v2 (analisa 50 trade: banyak SL di skor 60-73)
    "score_override": 92,           # skor utk override +1 slot (mode AUTO)
    "max_spread_pct": 0.30,         # filter likuiditas: spread maksimum
    "min_depth_x": 5,               # depth bid top-10 >= N x ukuran posisi
    "scan_interval": 60,            # detik antar siklus scan
    "pos_size_pct": 30,             # % modal per posisi
    "cooldown_min": 120,            # cooldown re-entry per pair (menit) — 2 jam, max 1x re-entry
    "top_n_pairs": 100,
    "time_stop_h": 4,               # tutup posisi stagnan setelah N jam
    "max_size_idr": 0,              # cap ukuran posisi (Rp), 0 = tanpa cap
    # #2 adaptasi sizing skor tinggi (high-conviction)
    "high_conv_enable": True,
    "high_conv_score": 90,          # skor >= ini -> size dinaikkan
    "high_conv_pct": 10,            # % ekuitas saat high-conviction
    # #5 kurs otomatis
    "kurs_auto": True,              # ambil kurs USD/IDR jam 06:00 & 18:00
}

STABLE_BASES = {"USDC", "FDUSD", "TUSD", "DAI", "BUSD", "USDP", "EUR", "IDRT", "PAXG"}
LEVERAGED_HINTS = ("UP", "DOWN", "BULL", "BEAR")
# Pair yang sering SL — diblacklist sementara (update sesuai performa log)
BLACKLISTED_PAIRS = {
    # Auto-generated oleh auto_analyzer.py — 2026-06-22
    # Pair dengan WR <35% DAN P&L < Rp 30,000 (min 5 trade)
    "ADAUSDT",
    "AVAXUSDT",
    "CRVUSDT",
    "FETUSDT",
    "ICPUSDT",
    "INJUSDT",
    "NEARUSDT",
    "ONDOUSDT",
    "PENGUUSDT",
    "SUIUSDT",
    "TRUMPUSDT",
    "ZECUSDT",
}

# Jam-jam golden hour (UTC) — WR 51% vs 24% di jam lain (analisa 452 trade)
# Jennifer patch 2026-06-20:
#   09:00 WIB = 02:00 UTC → WR 67% ✅  |  23:00 WIB = 16:00 UTC → WR 65% ✅
#   15:00 WIB = 08:00 UTC → WR 42% ✅  |  20:00 WIB = 13:00 UTC → WR 47% ✅
#   19:00 WIB = 12:00 UTC → WR 71% ✅
GOLDEN_HOURS_UTC = {1, 2, 8, 12, 13, 16, 17, 21}  # Auto-updated 2026-06-22
BAD_HOURS_UTC = {3, 4, 5, 6, 4, 14}  # Blacklist: 03-06 UTC + 04 UTC (11 WIB, WR 5%) + 14 UTC (21 WIB, 59 trade WR 24%)

# Host data pasar — dicoba berurutan, otomatis pindah jika gagal/terblokir ISP.
# data-api.binance.vision = mirror resmi khusus data publik (tanpa trading).
MARKET_HOSTS = [
    "https://api.binance.com",
    "https://data-api.binance.vision",
    "https://api.binance.me",
    "https://api1.binance.com",
]
BINANCE_API = MARKET_HOSTS[0]  # kompatibilitas
TOKO_BASE = "https://www.tokocrypto.com"          # order LIVE via Tokocrypto Open API
COINGECKO = "https://api.coingecko.com/api/v3"
FNG_API = "https://api.alternative.me/fng/"
CRYPTOPANIC = "https://cryptopanic.com/api/v1/posts/"

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
TRADE_LOG = os.path.join(LOG_DIR, "zalfarkana_trades.csv")
TRADE_EVENT_REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_event_report.py")

BULLISH_WORDS = ("partnership", "listing", "upgrade", "mainnet", "etf", "adoption",
                 "integration", "burn", "buyback", "approval", "record", "surge")
BEARISH_WORDS = ("hack", "exploit", "lawsuit", "sec sues", "delist", "rug",
                 "bankrupt", "outage", "dump", "investigation", "ban")


# ============================================================
# INDIKATOR TEKNIKAL (perhitungan identik standar TradingView)
# ============================================================
def ema(values, period):
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100 - (100 / (1 + rs))


def macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return None, None, None
    ef, es = ema(closes, fast), ema(closes, slow)
    ef = ef[len(ef) - len(es):]
    line = [a - b for a, b in zip(ef, es)]
    sig = ema(line, signal)
    if not sig:
        return None, None, None
    line = line[len(line) - len(sig):]
    hist = [a - b for a, b in zip(line, sig)]
    return line[-1], sig[-1], hist


def bollinger(closes, period=20, mult=2.0):
    if len(closes) < period:
        return None, None, None
    win = closes[-period:]
    mid = sum(win) / period
    var = sum((c - mid) ** 2 for c in win) / period
    sd = var ** 0.5
    return mid - mult * sd, mid, mid + mult * sd


def atr_pct(highs, lows, closes, period=14):
    """ATR sebagai % harga: rentang gerak rata-rata per candle."""
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    a = sum(trs[:period]) / period
    for t in trs[period:]:
        a = (a * (period - 1) + t) / period
    return a / closes[-1] * 100 if closes[-1] else None


# ============================================================
# DATA PASAR & DATA EKSTERNAL
# ============================================================
class MarketData:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "Zalfarkana/1.2"})
        self._cg_cache = {}
        self._cg_time = 0
        self._cg_ath_cache = {}    # symbol -> {ath, ath_date, ath_pct}
        self._cg_ath_time = 0
        self._fng = (50, 0)
        self._trending = (set(), 0)
        self._host_idx = 0  # host data pasar yang sedang aktif

    def _get(self, url, params=None, timeout=10):
        r = self.s.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def _market_get(self, path, params=None):
        """GET endpoint /api/v3/* dengan fallback antar host (anti blokir ISP)."""
        last_err = None
        for i in range(len(MARKET_HOSTS)):
            idx = (self._host_idx + i) % len(MARKET_HOSTS)
            try:
                out = self._get(f"{MARKET_HOSTS[idx]}{path}", params)
                if idx != self._host_idx:
                    self._host_idx = idx  # kunci host yang berhasil
                return out
            except Exception as e:
                last_err = e
        raise last_err

    def top_pairs(self, n=50):
        data = self._market_get("/api/v3/ticker/24hr")
        rows = []
        for t in data:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            base = sym[:-4]
            if base in STABLE_BASES or any(base.endswith(h) for h in LEVERAGED_HINTS):
                continue
            try:
                rows.append((sym, base, float(t["quoteVolume"]), float(t["lastPrice"])))
            except (KeyError, ValueError):
                continue
        rows.sort(key=lambda x: x[2], reverse=True)
        return rows[:n]

    def klines(self, symbol, interval="5m", limit=120):
        data = self._market_get("/api/v3/klines",
                                {"symbol": symbol, "interval": interval, "limit": limit})
        opens = [float(k[1]) for k in data]
        highs = [float(k[2]) for k in data]
        lows = [float(k[3]) for k in data]
        closes = [float(k[4]) for k in data]
        vols = [float(k[5]) for k in data]
        return closes, vols, highs, lows, opens

    def book(self, symbol, limit=10):
        d = self._market_get("/api/v3/depth", {"symbol": symbol, "limit": limit})
        bids = [(float(p), float(q)) for p, q in d["bids"]]
        asks = [(float(p), float(q)) for p, q in d["asks"]]
        if not bids or not asks:
            return None
        bid, ask = bids[0][0], asks[0][0]
        spread = (ask - bid) / ask * 100
        bid_depth_usdt = sum(p * q for p, q in bids)
        return {"bid": bid, "ask": ask, "spread_pct": spread, "bid_depth": bid_depth_usdt}

    def price(self, symbol):
        d = self._market_get("/api/v3/ticker/price", {"symbol": symbol})
        return float(d["price"])

    # ---------- CoinGecko (fundamental & tokenomik) ----------
    def coingecko_map(self):
        if time.time() - self._cg_time < 900 and self._cg_cache:
            return self._cg_cache
        try:
            data = self._get(f"{COINGECKO}/coins/markets",
                             {"vs_currency": "usd", "order": "market_cap_desc",
                              "per_page": 250, "page": 1}, timeout=15)
            m = {}
            for c in data:
                sym = c.get("symbol", "").upper()
                if sym in m:
                    continue
                mc = c.get("market_cap") or 0
                fdv = c.get("fully_diluted_valuation") or 0
                m[sym] = {
                    "rank": c.get("market_cap_rank") or 999,
                    "chg24": c.get("price_change_percentage_24h") or 0.0,
                    "fdv_mc": (fdv / mc) if mc else 99,
                    "id": c.get("id", ""),  # CoinGecko ID buat fetch ATH
                }
            self._cg_cache, self._cg_time = m, time.time()
        except Exception:
            pass
        return self._cg_cache

    def coingecko_ath(self, base):
        """ATH coin via CoinGecko. Cache 1 jam. Return {ath, ath_date, ath_pct} atau None."""
        now = time.time()
        if base in self._cg_ath_cache and now - self._cg_ath_cache[base].get("_t", 0) < 3600:
            return self._cg_ath_cache[base]
        cg = self.coingecko_map().get(base)
        if not cg or not cg.get("id"):
            return None
        try:
            d = self._get(f"{COINGECKO}/coins/{cg['id']}", {
                "localization": "false", "tickers": "false",
                "community_data": "false", "developer_data": "false",
            }, timeout=10)
            md = d.get("market_data") or {}
            ath_usd = md.get("ath", {}).get("usd")
            ath_date = md.get("ath_date", {}).get("usd")
            ath_pct = md.get("ath_change_percentage", {}).get("usd")
            if ath_usd:
                entry = {"ath": ath_usd, "ath_date": ath_date,
                         "ath_pct": ath_pct, "_t": now}
                self._cg_ath_cache[base] = entry
                return entry
        except Exception:
            pass
        return None

    def fear_greed(self):
        if time.time() - self._fng[1] < 1800:
            return self._fng[0]
        try:
            d = self._get(FNG_API, {"limit": 1})
            v = int(d["data"][0]["value"])
            self._fng = (v, time.time())
        except Exception:
            pass
        return self._fng[0]

    def trending(self):
        if time.time() - self._trending[1] < 900:
            return self._trending[0]
        try:
            d = self._get(f"{COINGECKO}/search/trending")
            syms = {c["item"]["symbol"].upper() for c in d.get("coins", [])}
            self._trending = (syms, time.time())
        except Exception:
            pass
        return self._trending[0]

    def fetch_usd_idr(self):
        """Ambil kurs USD->IDR dari sumber gratis (tanpa API key).
        Beberapa fallback agar tahan gangguan. Return float atau None."""
        sources = [
            ("https://open.er-api.com/v6/latest/USD",
             lambda j: j["rates"]["IDR"]),
            ("https://api.exchangerate.host/latest?base=USD&symbols=IDR",
             lambda j: j["rates"]["IDR"]),
            ("https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json",
             lambda j: j["usd"]["idr"]),
        ]
        for url, pick in sources:
            try:
                j = self._get(url, timeout=12)
                v = float(pick(j))
                if 10_000 < v < 30_000:   # sanity check kurs wajar
                    return v
            except Exception:
                continue
        return None

    def news_score(self, base, cp_key):
        """Skor berita -10..+10 via CryptoPanic (opsional)."""
        if not cp_key:
            return 0, "berita: nonaktif"
        try:
            d = self._get(CRYPTOPANIC, {"auth_token": cp_key, "currencies": base,
                                        "public": "true"}, timeout=10)
            score = 0
            for post in d.get("results", [])[:15]:
                title = (post.get("title") or "").lower()
                if any(w in title for w in BULLISH_WORDS):
                    score += 2
                if any(w in title for w in BEARISH_WORDS):
                    score -= 3
            score = max(-10, min(10, score))
            return score, f"berita: {score:+d}"
        except Exception:
            return 0, "berita: gagal diambil"


# ============================================================
# MESIN SINYAL — skor 0..100
# ============================================================
class SignalEngine:
    def __init__(self, md: MarketData, cfg):
        self.md = md
        self.cfg = cfg

    def score(self, symbol, base, cp_key=""):
        # Filter blacklist pair sering SL
        if symbol in BLACKLISTED_PAIRS:
            return None

        # FILTER 2: Golden Hours — hanya entry di jam terbaik (analisa 442 trade)
        # Golden hours WR=51% vs non-golden WR=24%
        # Jam 03-06 UTC selalu skip (blacklisted 2026-06-20)
        current_hour_utc = datetime.utcnow().hour
        if current_hour_utc in BAD_HOURS_UTC:
            return None
        if current_hour_utc not in GOLDEN_HOURS_UTC:
            return None

        reasons = []
        try:
            closes, vols, highs, lows, opens = self.md.klines(symbol)
        except Exception:
            return None
        if len(closes) < 60:
            return None
        price = closes[-1]
        s = 0

        # ---- Konfirmasi pembalikan (FILTER KERAS, v1.4) ----
        # Jangan beli pisau jatuh: candle CLOSED terakhir (indeks -2,
        # karena -1 masih berjalan) harus hijau DAN lebih tinggi dari
        # candle sebelumnya — bukti pantulan sudah dimulai.
        # Jennifer v2: kalo E↓ (EMA turun), butuh 2 candle hijau berturut-turut
        # untuk filter dead cat bounce (analisa 50 trade: E↓ single candle = banyak SL)
        e9_tmp = ema(closes, 9)
        e21_tmp = ema(closes, 21)
        ema_down = bool(e9_tmp and e21_tmp and e9_tmp[-1] <= e21_tmp[-1])
        candle1_ok = closes[-2] > opens[-2] and closes[-2] > closes[-3]
        if not candle1_ok:
            return None
        if ema_down:
            # E↓: butuh 2 candle hijau berturut-turut (dead cat bounce protection)
            if not (closes[-3] > opens[-3] and closes[-3] > closes[-4]):
                return None
            reasons.append("2 candle hijau (E↓) ✓")
        else:
            reasons.append("candle pembalikan ✓")

        # ---- Volatilitas / kelayakan TP (maks 15, FILTER KERAS) ----
        # Jennifer kalibrasi: naikkan threshold ratio karena TP sekarang 2%
        # proj/tp harus >= 1.2 agar peluang capai TP cukup besar
        ap = atr_pct(highs, lows, closes)
        if ap is None:
            return None
        n_candles = max(1, self.cfg.get("time_stop_h", 4) * 12)  # candle 5m
        proj = ap * (n_candles ** 0.5)          # proyeksi rentang gerak
        ratio = proj / max(self.cfg["tp_pct"], 0.1)
        if ratio < 1.0:
            return None  # volatilitas tidak cukup untuk capai TP 2% dalam window
        if ratio >= 4:
            s += 5; reasons.append(f"vol x{ratio:.1f} TP (terlalu liar, hati2 SL)")
        elif ratio >= 2.0:
            s += 15; reasons.append(f"vol x{ratio:.1f} TP (bagus)")
        elif ratio >= 1.5:
            s += 12; reasons.append(f"vol x{ratio:.1f} TP")
        elif ratio >= 1.2:
            s += 8; reasons.append(f"vol x{ratio:.1f} TP")
        else:
            s += 4; reasons.append(f"vol x{ratio:.1f} TP (marginal)")

        # ---- Teknikal (maks 55) ----
        snap = {"atr_pct": round(ap, 3), "vol_ratio_tp": round(ratio, 2)}
        r = rsi(closes)
        snap["rsi"] = round(r, 1) if r is not None else None
        if r is not None:
            if r < 35:
                s += 20; reasons.append(f"RSI {r:.0f} oversold kuat")  # Jennifer: naikkan bobot oversold +20
            elif r < 45:
                s += 12; reasons.append(f"RSI {r:.0f} rendah")         # Jennifer: naikkan dari +8
            elif r > 72:
                s -= 15; reasons.append(f"RSI {r:.0f} overbought")     # Jennifer: naikkan penalti -15
        ml, msig, hist = macd(closes)
        snap["macd_hist"] = round(hist[-1], 6) if hist else None
        macd_positive = bool(hist and hist[-1] > 0)

        # FILTER 3: RSI<45 + MACD>0 wajib — Jennifer kalibrasi dari analisa 452 trade
        # RSI<45+MACD>0 = setup paling konsisten
        # RSI 45-60 = zona ambigu, skip (WR hampir sama antara TP & SL)
        # Tambahan: RSI<35 = oversold kuat, beri bobot lebih (sudah di atas: +15)
        r_val = r if r is not None else 50
        if r_val is not None and 45 <= r_val <= 60:
            return None  # RSI zona ambigu, skip entry
        if not (r_val < 50 and macd_positive):
            return None
        reasons.append("RSI<45+MACD>0 ✓")

        if hist and len(hist) >= 3 and hist[-1] > hist[-2] > hist[-3]:
            s += 10; reasons.append("MACD hist naik")
            if hist[-2] < 0 <= hist[-1]:
                s += 3; reasons.append("MACD cross bullish")
        low, mid, up = bollinger(closes)
        # posisi harga dalam BB: 0=pita bawah, 1=pita atas
        snap["bb_pos"] = (round((price - low) / (up - low), 3)
                          if (low and up and up > low) else None)
        if low and price <= low * 1.005:
            s += 10; reasons.append("harga di pita bawah BB")
        e9, e21 = ema(closes, 9), ema(closes, 21)
        ema9_gt_21 = bool(e9 and e21 and e9[-1] > e21[-1])
        snap["ema9_gt_21"] = ema9_gt_21
        # FILTER 5: EMA paradox — dari analisa log:
        # EMA<21 + MACD>0 = WR 43%, P&L +Rp38k ✅
        # EMA>21 + MACD>0 = WR 35%, P&L -Rp355k ❌
        # Kurangi bobot EMA>21, naikkan bobot EMA<21+MACD>0
        if not ema9_gt_21 and macd_positive:
            s += 15; reasons.append("EMA<21+MACD>0 (high-prob setup)")
        elif ema9_gt_21 and macd_positive:
            s += 5; reasons.append("EMA>21+MACD>0 (reduced weight)")  # turun dari +10
        elif ema9_gt_21:
            s += 3; reasons.append("EMA9 > EMA21")  # tanpa MACD konfirmasi, nilai rendah
        vol_ratio = None
        if len(vols) > 21:
            avg_v = sum(vols[-21:-1]) / 20
            if avg_v > 0:
                vol_ratio = vols[-1] / avg_v
                # FILTER 4: Blokir spike volume >5x — WR hanya 25% (analisa log)
                if vol_ratio > 5:
                    return None  # spike volume berbahaya, skip
                elif vol_ratio > 1.5:
                    s += 10; reasons.append(f"volume x{vol_ratio:.1f}")
        snap["vol_x"] = round(vol_ratio, 2) if vol_ratio else None

        # ---- Fundamental / tokenomik (maks 15) ----
        # v1.3: rank MC = legitimasi saja (+5 flat utk top-150), bukan
        # keunggulan big-cap — big cap lambat bergerak, buruk utk scalping.
        cg = self.md.coingecko_map().get(base)
        if cg:
            if cg["rank"] <= 150:
                s += 5; reasons.append(f"rank MC #{cg['rank']}")
            if cg["fdv_mc"] <= 1.2:
                s += 5; reasons.append("FDV/MC sehat")
            if -5 <= cg["chg24"] <= 5:
                s += 5; reasons.append("24h stabil (bukan pump)")
            elif cg["chg24"] > 15:
                s -= 8; reasons.append(f"sudah pump {cg['chg24']:+.0f}%/24h")

        # ---- ATH Distance (maks 15) ----
        ath_data = self.md.coingecko_ath(base)
        ath_ratio = None
        if ath_data and ath_data.get("ath"):
            ath_price = ath_data["ath"]
            ath_ratio = price / ath_price  # 0.5 = 50% dari ATH
            snap["ath_ratio"] = round(ath_ratio, 3)
            if ath_ratio <= 0.10:
                s += 15; reasons.append(f"ATH discount 90%+ (${ath_price:.1f})")
                # Bonus: bukan dead coin kalo MC rank oke
                if cg and cg["rank"] <= 200:
                    s += 5; reasons.append("ATH bonus: MC top-200 ✓")
            elif ath_ratio <= 0.30:
                s += 10; reasons.append(f"ATH -{(1-ath_ratio)*100:.0f}% (${ath_price:.1f})")
            elif ath_ratio <= 0.50:
                s += 5; reasons.append(f"ATH -{(1-ath_ratio)*100:.0f}% diskon")
            elif ath_ratio > 0.70:
                s -= 5; reasons.append(f"dekat ATH (ratio {ath_ratio:.0%})")

        # ---- Sentimen (maks 15) ----
        fng = self.md.fear_greed()
        if 25 <= fng <= 60:
            s += 10; reasons.append(f"F&G {fng} netral-fear")
        elif fng < 25:
            s += 5; reasons.append(f"F&G {fng} extreme fear")
        elif fng > 80:
            s -= 5; reasons.append(f"F&G {fng} extreme greed")
        if base in self.md.trending():
            s += 5; reasons.append("trending CoinGecko")

        # ---- Berita (maks +-10) ----
        ns, ntxt = self.md.news_score(base, cp_key)
        s += ns
        reasons.append(ntxt)

        snap["fng"] = fng
        return {"symbol": symbol, "base": base, "price": price,
                "score": max(0, min(100, s)), "reasons": reasons,
                "snap": snap}


# ============================================================
# EKSEKUSI ORDER
# ============================================================
class PaperBroker:
    """Simulasi: fill di harga ask/bid + fee taker."""
    def __init__(self, fee_pct):
        self.fee = fee_pct / 100

    def buy(self, symbol, usdt, ask):
        qty = usdt * (1 - self.fee) / ask
        return {"qty": qty, "fill": ask}

    def sell(self, symbol, qty, bid):
        return {"proceeds": qty * bid * (1 - self.fee), "fill": bid}


class CcxtTokoBroker:
    """
    Order LIVE via CCXT (github.com/ccxt/ccxt) — modul resmi Tokocrypto.
    Signature HMAC, presisi qty, format simbol, dan rate-limit ditangani
    library yang dirawat ribuan kontributor — jauh lebih andal daripada
    implementasi manual. Format simbol CCXT: BTC/USDT.
    """
    def __init__(self, api_key, api_secret, fee_pct):
        if ccxt is None:
            raise RuntimeError("Mode LIVE butuh CCXT. Jalankan: pip install ccxt")
        self.fee = fee_pct / 100
        self.ex = ccxt.tokocrypto({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        })
        self.ex.load_markets()

    @staticmethod
    def ccxt_symbol(binance_symbol):
        return binance_symbol[:-4] + "/USDT"

    def usdt_free(self):
        bal = self.ex.fetch_balance()
        return float((bal.get("USDT") or {}).get("free") or 0)

    def buy_market(self, binance_symbol, quote_usdt):
        sym = self.ccxt_symbol(binance_symbol)
        t = self.ex.fetch_ticker(sym)
        price = float(t.get("ask") or t.get("last"))
        amount = float(self.ex.amount_to_precision(sym, quote_usdt / price))
        o = self.ex.create_order(sym, "market", "buy", amount)
        filled = float(o.get("filled") or amount)
        avg = float(o.get("average") or price)
        # konservatif: sisihkan fee dari qty yang dianggap bisa dijual
        return {"qty": filled * (1 - self.fee), "fill": avg}

    def sell_market(self, binance_symbol, qty):
        sym = self.ccxt_symbol(binance_symbol)
        amount = float(self.ex.amount_to_precision(sym, qty))
        o = self.ex.create_order(sym, "market", "sell", amount)
        return {"fill": float(o.get("average") or 0)}


# ============================================================
# MANAJEMEN RISIKO & POSISI
# ============================================================
class Position:
    def __init__(self, symbol, qty, entry, usdt_in, score, is_override=False, snap=None):
        self.symbol = symbol
        self.qty = qty
        self.entry = entry
        self.usdt_in = usdt_in
        self.score = score
        self.is_override = is_override
        self.opened = datetime.now()
        self.snap = snap or {}


class RiskManager:
    def __init__(self, cfg):
        self.cfg = cfg
        self.day = date.today()
        self.realized_pl_idr = 0.0
        self.override_in_use = False

    def _roll_day(self):
        if date.today() != self.day:
            self.day = date.today()
            self.realized_pl_idr = 0.0

    def record_pl(self, pl_idr):
        self._roll_day()
        self.realized_pl_idr += pl_idr

    def daily_stop_hit(self):
        self._roll_day()
        return self.realized_pl_idr <= -abs(self.cfg["daily_loss_limit_idr"])

    def can_open(self, open_count, score):
        """Return (boleh, pakai_override)."""
        if self.daily_stop_hit():
            return False, False
        if open_count < self.cfg["max_open"]:
            return True, False
        if (not self.override_in_use and score >= self.cfg["score_override"]
                and open_count < self.cfg["max_open"] + 1):
            return True, True
        return False, False


# ============================================================
# BOT UTAMA (thread)
# ============================================================
class ZalfarkanaBot(threading.Thread):
    def __init__(self, cfg, ui_q, mode_auto=True, paper=True,
                 api_key="", api_secret="", cp_key=""):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.q = ui_q
        self.mode_auto = mode_auto
        self.paper = paper
        self.cp_key = cp_key
        self.md = MarketData()
        self.engine = SignalEngine(self.md, cfg)
        self.risk = RiskManager(cfg)
        self.paper_broker = PaperBroker(cfg["fee_pct"])
        self.live_broker = (CcxtTokoBroker(api_key, api_secret, cfg["fee_pct"])
                            if (not paper and api_key) else None)
        self.positions = {}
        self.cooldown = {}
        self.reentry_count = {}  # pair -> jumlah re-entry setelah SL (max 1)
        self.stop_flag = threading.Event()
        self.kill_flag = threading.Event()
        self.manual_buy_q = queue.Queue()
        self.usdt_balance = cfg["modal_idr"] / cfg["usdt_idr"]
        self.total_pl_idr = 0.0   # akumulasi P/L semua trade -> basis COMPOUNDING
        self.win_count = 0
        self.closed_count = 0
        self.last_signals = []

    def floating_pl_idr(self):
        """Floating/unrealized P/L semua posisi terbuka (#4).
        Jika kill switch ditekan sekarang, kira-kira inilah hasilnya
        (sudah memperhitungkan fee jual)."""
        total = 0.0
        for pos in list(self.positions.values()):
            try:
                p = self.md.price(pos.symbol)
            except Exception:
                continue
            proceeds = pos.qty * p * (1 - self.cfg["fee_pct"] / 100)
            total += self.idr(proceeds - pos.usdt_in)
        return total

    # ---------- util ----------
    def log(self, msg, kind="log"):
        self.q.put((kind, f"[{datetime.now():%H:%M:%S}] {msg}" if kind == "log" else msg))

    def idr(self, usdt):
        return usdt * self.cfg["usdt_idr"]

    def equity_idr(self):
        """Ekuitas berjalan = modal awal + akumulasi P/L. Basis compounding."""
        return self.cfg["modal_idr"] + self.total_pl_idr

    def pos_size_usdt(self, manual_idr=0, score=None):
        """COMPOUNDING: size dihitung dari ekuitas berjalan, bukan modal awal.
        manual_idr > 0 (mode SIGNAL) = ukuran posisi ditentukan pengguna.
        ADAPTIF (#2): bila score >= ambang high-conviction, persen size
        dinaikkan otomatis ke high_conv_pct (mis. 10%). Pagar: tidak pernah
        melebihi cap, dan daily-loss-limit tetap mengikat di can_open()."""
        if manual_idr > 0:
            size_idr = manual_idr
        else:
            pct = self.cfg["pos_size_pct"]
            if (self.cfg.get("high_conv_enable") and score is not None
                    and score >= self.cfg.get("high_conv_score", 90)):
                pct = self.cfg.get("high_conv_pct", 10)
            size_idr = self.equity_idr() * pct / 100
        cap = self.cfg.get("max_size_idr", 0)
        if cap > 0:
            size_idr = min(size_idr, cap)
        return max(size_idr, 0) / self.cfg["usdt_idr"]

    def market_regime_ok(self):
        """Filter rezim pasar (v1.4): BTC = kompas. Saat BTC turun,
        semua altcoin ikut turun serentak — long apa pun ditahan.
        Syarat hijau: BTC 1 jam terakhir > -0,3% DAN EMA20 tidak menurun.
        Cache 60 detik agar hemat request."""
        now = time.time()
        if now - getattr(self, "_regime_ts", 0) < 60:
            return self._regime_ok, self._regime_desc
        ok, desc = True, "BTC ?"
        try:
            closes, _, _, _, _ = self.md.klines("BTCUSDT", "5m", 40)
            chg_1h = (closes[-1] - closes[-13]) / closes[-13] * 100
            e20 = ema(closes, 20)
            slope_ok = bool(e20) and e20[-1] >= e20[-3]
            ok = chg_1h > -1.0 and slope_ok
            desc = (f"BTC 1j {chg_1h:+.2f}%, EMA20 "
                    f"{'naik/datar' if slope_ok else 'menurun'}")
        except Exception:
            pass  # data gagal -> jangan blokir, biarkan filter lain bekerja
        self._regime_ok, self._regime_desc, self._regime_ts = ok, desc, now
        return ok, desc

    # ---------- entry / exit ----------
    def open_position(self, sig, is_override=False, manual_idr=0):
        symbol = sig["symbol"]
        book = self.md.book(symbol)
        if not book:
            return
        size = self.pos_size_usdt(manual_idr, sig.get("score"))
        if self.paper:
            if self.usdt_balance < size:
                self.log("Saldo paper tidak cukup utk posisi baru.")
                return
            fill = self.paper_broker.buy(symbol, size, book["ask"])
            self.usdt_balance -= size
            qty, entry = fill["qty"], fill["fill"]
        else:
            try:
                if self.live_broker.usdt_free() < size:
                    self.log(f"Saldo USDT Tokocrypto tidak cukup utk {symbol}.")
                    return
                res = self.live_broker.buy_market(symbol, size)
                qty, entry = res["qty"], res["fill"]
            except Exception as e:
                self.log(f"GAGAL BUY LIVE {symbol}: {e}")
                return
        self.positions[symbol] = Position(symbol, qty, entry, size,
                                          sig["score"], is_override,
                                          snap=sig.get("snap"))
        if is_override:
            self.risk.override_in_use = True
        tag = " [OVERRIDE +1]" if is_override else ""
        hc = ""
        if (self.cfg.get("high_conv_enable") and manual_idr == 0
                and sig.get("score", 0) >= self.cfg.get("high_conv_score", 90)):
            hc = f" [HIGH-CONV {self.cfg.get('high_conv_pct',10)}%]"
        self.log(f"BUY {symbol} @ {entry:.6g} | skor {sig['score']} | "
                 f"Rp {self.idr(size):,.0f}{tag}{hc}".replace(",", "."))
        self._emit_trade_event("OPEN", symbol, entry, entry, None, 0.0, 0.0, size)
        self.q.put(("positions", None))

    def close_position(self, pos, reason, price=None):
        symbol = pos.symbol
        book = self.md.book(symbol)
        bid = price or (book["bid"] if book else pos.entry)
        if self.paper:
            out = self.paper_broker.sell(symbol, pos.qty, bid)
            proceeds = out["proceeds"]
            self.usdt_balance += proceeds
        else:
            try:
                res = self.live_broker.sell_market(symbol, pos.qty)
                fill = res["fill"] or bid
                bid = fill
                proceeds = pos.qty * fill * (1 - self.cfg["fee_pct"] / 100)
            except Exception as e:
                self.log(f"GAGAL SELL LIVE {symbol}: {e} — COBA MANUAL DI APP TOKOCRYPTO!")
                return
        pl_usdt = proceeds - pos.usdt_in
        pl_idr = self.idr(pl_usdt)
        self.risk.record_pl(pl_idr)
        self.total_pl_idr += pl_idr   # ekuitas ter-update -> trade berikut compounding
        if pos.is_override:
            self.risk.override_in_use = False
        self.positions.pop(symbol, None)
        self.cooldown[symbol] = time.time() + self.cfg["cooldown_min"] * 60
        # Track re-entry: saat SL, catat 1 re-entry sudah terpakai (max 1x)
        if "SL" in reason or "stop loss" in reason.lower():
            self.reentry_count[symbol] = self.reentry_count.get(symbol, 0) + 1
        else:
            # TP atau time-stop: reset re-entry counter
            self.reentry_count.pop(symbol, None)
        self._write_trade(pos, bid, pl_idr, reason)
        self._emit_trade_event("CLOSE", symbol, pos.entry, bid, bid, pl_idr,
                               (pl_idr / pos.usdt_in * 100) if pos.usdt_in else 0.0,
                               pos.usdt_in)
        # win-rate berjalan
        self.closed_count += 1
        if pl_idr > 0:
            self.win_count += 1
        wr = self.win_count / self.closed_count * 100
        # Kirim Telegram real-time
        self._send_trade_tg(pos, bid, pl_idr, reason)
        sign = "+" if pl_idr >= 0 else ""
        kind = "win" if pl_idr > 0 else "loss"
        self.q.put((kind, f"[{datetime.now():%H:%M:%S}] SELL {symbol} @ {bid:.6g} "
                          f"({reason}) | P/L {sign}Rp {pl_idr:,.0f} | "
                          f"WR {wr:.0f}% ({self.win_count}/{self.closed_count})"
                          .replace(",", ".")))
        self.q.put(("positions", None))
        self.q.put(("stats", None))

    def _emit_trade_event(self, status, symbol, entry, current, exit_price, pnl_idr, pnl_pct, qty_rp):
        try:
            pnl_24h = 0.0
            if os.path.exists(TRADE_LOG):
                since = datetime.now() - timedelta(hours=24)
                with open(TRADE_LOG, newline="", encoding="utf-8") as f:
                    r = csv.reader(f)
                    for row in r:
                        if len(row) < 8 or row[0].strip() == "waktu_buka":
                            continue
                        try:
                            dt = datetime.strptime(row[1].strip(), "%Y-%m-%d %H:%M:%S")
                            if dt >= since:
                                pnl_24h += float(row[7])
                        except Exception:
                            pass
            side = "BUY" if current >= entry else "SELL"
            cmd = [sys.executable, TRADE_EVENT_REPORT,
                   "--symbol", symbol,
                   "--side", side,
                   "--entry", str(entry),
                   "--current", str(current),
                   "--pnl", str(pnl_idr),
                   "--pnl-pct", str(pnl_pct),
                   "--pnl-24h", str(pnl_24h),
                   "--status", status,
                   "--qty-rp", str(qty_rp)]
            if exit_price is not None:
                cmd += ["--exit-price", str(exit_price)]
            out = subprocess.check_output(cmd, text=True).strip()
            self.log(out)
        except Exception as e:
            self.log(f"Gagal emit trade event: {e}")

    def _write_trade(self, pos, exit_price, pl_idr, reason):
        new = not os.path.exists(TRADE_LOG)
        sn = pos.snap or {}
        with open(TRADE_LOG, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["waktu_buka", "waktu_tutup", "pair", "entry", "exit",
                            "qty", "modal_usdt", "pl_idr", "alasan", "skor",
                            "override", "mode",
                            # snapshot indikator saat ENTRY (#6)
                            "rsi", "macd_hist", "bb_pos", "atr_pct",
                            "ema9_gt_21", "vol_x", "vol_ratio_tp", "fng"])
            w.writerow([pos.opened.strftime("%Y-%m-%d %H:%M:%S"),
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        pos.symbol, f"{pos.entry:.8f}", f"{exit_price:.8f}",
                        f"{pos.qty:.8f}", f"{pos.usdt_in:.2f}", f"{pl_idr:.0f}",
                        reason, pos.score, pos.is_override,
                        "PAPER" if self.paper else "LIVE",
                        sn.get('rsi'), sn.get('macd_hist'), sn.get('bb_pos'),
                        sn.get('atr_pct'), sn.get('ema9_gt_21'), sn.get('vol_x'),
                        sn.get('vol_ratio_tp'), sn.get('fng')])

    def _send_trade_tg(self, pos, exit_price, pl_idr, reason):
        try:
            import requests
            HERE = os.path.dirname(os.path.abspath(__file__))
            TOKEN_FILE = os.path.join(HERE, ".telegram_token")
            CHAT_FILE = os.path.join(HERE, ".telegram_chat_id")
            with open(TOKEN_FILE, encoding="utf-8") as f:
                token = f.read().strip()
            try:
                with open(CHAT_FILE, encoding="utf-8") as f:
                    chat_id = f.read().strip()
            except FileNotFoundError:
                chat_id = os.environ.get("ZALFARKANA_CHAT_ID", "7216692716")
            sn = pos.snap or {}
            rsi = sn.get('rsi', '?')
            macd_h = sn.get('macd_hist', 0)
            macd_s = '+' if (isinstance(macd_h, (int, float)) and macd_h > 0) else '-'
            ema_v = sn.get('ema9_gt_21')
            ema_s = '\u2191' if (ema_v == True or ema_v == 'True') else '\u2193'
            skor = pos.score or 0
            pair = pos.symbol.replace('USDT', '').ljust(8)
            pl_k = pl_idr // 1000 if abs(pl_idr) >= 1000 else pl_idr
            sign = '+' if pl_idr >= 0 else ''
            reason_s = reason[:2].upper()
            waktu = datetime.now().strftime('%m/%d %H:%M')
            msg = f'{waktu} {pair} {reason_s} {sign}{pl_k}k RSI {rsi} M{macd_s} E{ema_s} s{skor}'
            requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                json={'chat_id': chat_id, 'text': msg}, timeout=5)
        except Exception:
            pass

    # ---------- loop ----------
    def manage_positions(self):
        for sym in list(self.positions):
            pos = self.positions[sym]
            try:
                p = self.md.price(sym)
            except Exception:
                continue
            chg = (p - pos.entry) / pos.entry * 100
            held_h = (datetime.now() - pos.opened).total_seconds() / 3600
            if chg >= self.cfg["tp_pct"]:
                self.close_position(pos, "TP", p)
            elif chg <= -self.cfg["sl_pct"]:
                self.close_position(pos, "SL", p)
            elif held_h >= self.cfg.get("time_stop_h", 4):
                self.close_position(pos, "TIME", p)  # posisi stagnan, bebaskan modal

    def kill_all(self):
        self.log("KILL SWITCH — menutup semua posisi...")
        for sym in list(self.positions):
            self.close_position(self.positions[sym], "KILL")
        self.kill_flag.clear()

    def scan(self):
        try:
            pairs = self.md.top_pairs(self.cfg["top_n_pairs"])
        except Exception as e:
            self.log(f"Gagal ambil daftar pair: {e}")
            return
        signals = []
        for sym, base, vol, _ in pairs:
            if self.stop_flag.is_set() or self.kill_flag.is_set():
                return
            if sym in self.positions or self.cooldown.get(sym, 0) > time.time():
                continue
            # Max 1x re-entry setelah SL — blokir re-entry kedua
            if self.reentry_count.get(sym, 0) >= 1:
                continue
            book = self.md.book(sym)
            if not book:
                continue
            # FILTER LIKUIDITAS
            if book["spread_pct"] > self.cfg["max_spread_pct"]:
                continue
            if book["bid_depth"] < self.cfg["min_depth_x"] * self.pos_size_usdt():
                continue
            sig = self.engine.score(sym, base, self.cp_key)
            if sig:
                signals.append(sig)
            time.sleep(0.15)  # jaga rate limit
        signals.sort(key=lambda x: x["score"], reverse=True)
        self.last_signals = signals[:15]
        self.q.put(("signals", self.last_signals))

        if self.mode_auto:
            regime_ok, desc = self.market_regime_ok()
            if not regime_ok:
                self.log(f"Rezim pasar NEGATIF ({desc}) — semua entry ditahan.")
                return
            opened_this_cycle = 0
            for sig in signals:
                if sig["score"] < self.cfg["score_entry"]:
                    break
                if opened_this_cycle >= 2:
                    self.log("Batas 2 entry/siklus tercapai — sisanya tunggu scan berikut.")
                    break
                ok, override = self.risk.can_open(len(self.positions), sig["score"])
                if not ok:
                    if self.risk.daily_stop_hit():
                        self.log("DAILY LOSS LIMIT tercapai — entry dihentikan hari ini.")
                    break
                before = len(self.positions)
                self.open_position(sig, override)
                if len(self.positions) > before:
                    opened_this_cycle += 1

    def maybe_update_kurs(self):
        """#5: perbarui kurs USD/IDR otomatis pada jam 06:00 & 18:00 WIB.
        Tidak mengisi manual lagi. Cache slot agar tidak berulang."""
        if not self.cfg.get("kurs_auto"):
            return
        now = datetime.now()
        slot = None
        if now.hour == 6:
            slot = f"{now.date()}-06"
        elif now.hour == 18:
            slot = f"{now.date()}-18"
        if slot and slot != getattr(self, "_kurs_slot", None):
            v = self.md.fetch_usd_idr()
            if v:
                self.cfg["usdt_idr"] = v
                self._kurs_slot = slot
                self.log(f"Kurs USD/IDR diperbarui otomatis: Rp {v:,.0f}".replace(",", "."))
                self.q.put(("stats", None))
            else:
                self.log("Gagal ambil kurs otomatis — tetap pakai kurs sebelumnya.")

    def run(self):
        self.log(f"Zalfarkana mulai | mode {'AUTO' if self.mode_auto else 'SIGNAL'} | "
                 f"{'PAPER' if self.paper else 'LIVE'}")
        # ambil kurs sekali di awal jika mode auto-kurs aktif
        if self.cfg.get("kurs_auto"):
            v = self.md.fetch_usd_idr()
            if v:
                self.cfg["usdt_idr"] = v
                self.log(f"Kurs awal USD/IDR: Rp {v:,.0f}".replace(",", "."))
                self.q.put(("stats", None))
        last_scan = 0
        last_float = 0
        while not self.stop_flag.is_set():
            if self.kill_flag.is_set():
                self.kill_all()
            self.maybe_update_kurs()
            # eksekusi BUY manual dari mode SIGNAL
            try:
                while True:
                    sig, manual_idr = self.manual_buy_q.get_nowait()
                    regime_ok, desc = self.market_regime_ok()
                    if not regime_ok:
                        self.log(f"PERINGATAN: rezim pasar negatif ({desc}) — "
                                 f"tetap dieksekusi atas perintah Anda.")
                    ok, override = self.risk.can_open(len(self.positions), sig["score"])
                    if ok:
                        self.open_position(sig, False, manual_idr)
                    else:
                        self.log("Tidak bisa buka posisi (limit/daily stop).")
            except queue.Empty:
                pass
            self.manage_positions()
            # emit floating P/L tiap ~6 detik (#4)
            if time.time() - last_float >= 6:
                self.q.put(("float", self.floating_pl_idr()))
                last_float = time.time()
            if time.time() - last_scan >= self.cfg["scan_interval"]:
                if not self.risk.daily_stop_hit():
                    self.scan()
                last_scan = time.time()
            time.sleep(3)
        self.log("Bot berhenti.")


# ============================================================
# GUI
# ============================================================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PROJECT ZALFARKANA v1.5 — Bot Scalping Tokocrypto")
        self.geometry("1180x720")
        self.bot = None
        self.q = queue.Queue()
        self.vars = {}
        self._build()
        self.after(300, self._poll)

    # ---------- layout ----------
    def _build(self):
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        trade_tab = ttk.Frame(self.nb)
        bt_tab = ttk.Frame(self.nb)
        self.nb.add(trade_tab, text="  TRADING  ")
        self.nb.add(bt_tab, text="  BACKTEST  ")
        self._build_trading(trade_tab)
        self._build_backtest(bt_tab)

    def _build_trading(self, root):
        top = ttk.Frame(root, padding=8)
        top.pack(fill="x")

        def field(parent, label, key, default, width=12):
            f = ttk.Frame(parent)
            ttk.Label(f, text=label).pack(anchor="w")
            v = tk.StringVar(value=str(default))
            ttk.Entry(f, textvariable=v, width=width).pack()
            self.vars[key] = v
            f.pack(side="left", padx=4)

        field(top, "Modal (Rp)", "modal_idr", DEFAULTS["modal_idr"])
        field(top, "Max loss/hari (Rp)", "daily_loss_limit_idr", DEFAULTS["daily_loss_limit_idr"])
        field(top, "Max posisi", "max_open", DEFAULTS["max_open"], 6)
        field(top, "TP %", "tp_pct", DEFAULTS["tp_pct"], 6)
        field(top, "SL %", "sl_pct", DEFAULTS["sl_pct"], 6)
        field(top, "Size/posisi %", "pos_size_pct", DEFAULTS["pos_size_pct"], 6)
        field(top, "Size SIGNAL Rp (0=auto)", "manual_size_idr", 0, 12)
        field(top, "Cap size Rp (0=off)", "max_size_idr", DEFAULTS["max_size_idr"], 12)

        top2 = ttk.Frame(root, padding=(8, 0))
        top2.pack(fill="x")
        field(top2, "Skor entry", "score_entry", DEFAULTS["score_entry"], 6)
        field(top2, "Skor override", "score_override", DEFAULTS["score_override"], 6)
        field(top2, "Time-stop (jam)", "time_stop_h", DEFAULTS["time_stop_h"], 6)
        field(top2, "Pair scan (10-100)", "top_n_pairs", DEFAULTS["top_n_pairs"], 6)
        field(top2, "Skor high-conv", "high_conv_score", DEFAULTS["high_conv_score"], 6)
        field(top2, "High-conv size %", "high_conv_pct", DEFAULTS["high_conv_pct"], 6)
        field(top2, "Kurs USDT/IDR", "usdt_idr", 16300, 8)
        self.kurs_auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top2, text="Kurs auto (06/18)",
                        variable=self.kurs_auto_var).pack(side="left", padx=4)

        mid = ttk.Frame(root, padding=(8, 0))
        mid.pack(fill="x")
        self.mode_var = tk.StringVar(value="AUTO")
        self.paper_var = tk.StringVar(value="PAPER")
        ttk.Label(mid, text="Mode:").pack(side="left")
        ttk.Radiobutton(mid, text="AUTO (full otomatis)", value="AUTO",
                        variable=self.mode_var).pack(side="left")
        ttk.Radiobutton(mid, text="SIGNAL (saya yang pilih)", value="SIGNAL",
                        variable=self.mode_var).pack(side="left", padx=(0, 16))
        ttk.Label(mid, text="Dana:").pack(side="left")
        ttk.Radiobutton(mid, text="PAPER (simulasi)", value="PAPER",
                        variable=self.paper_var).pack(side="left")
        ttk.Radiobutton(mid, text="LIVE", value="LIVE",
                        variable=self.paper_var).pack(side="left", padx=(0, 16))
        ttk.Label(mid, text="API Key:").pack(side="left")
        self.vars["api_key"] = tk.StringVar()
        ttk.Entry(mid, textvariable=self.vars["api_key"], width=16, show="*").pack(side="left")
        ttk.Label(mid, text="Secret:").pack(side="left")
        self.vars["api_secret"] = tk.StringVar()
        ttk.Entry(mid, textvariable=self.vars["api_secret"], width=16, show="*").pack(side="left")
        ttk.Label(mid, text="CryptoPanic:").pack(side="left")
        self.vars["cp_key"] = tk.StringVar()
        ttk.Entry(mid, textvariable=self.vars["cp_key"], width=12, show="*").pack(side="left")

        btns = ttk.Frame(root, padding=8)
        btns.pack(fill="x")
        self.start_btn = ttk.Button(btns, text="▶ START", command=self.start_bot)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="⏸ STOP", command=self.stop_bot, state="disabled")
        self.stop_btn.pack(side="left", padx=4)
        self.kill_btn = tk.Button(btns, text="🛑 KILL SWITCH — TUTUP SEMUA POSISI",
                                  bg="#c0392b", fg="white", command=self.kill,
                                  state="disabled", font=("Arial", 10, "bold"))
        self.kill_btn.pack(side="left", padx=12)

        # panel statistik kanan: ekuitas, P/L hari ini, floating P/L
        statf = ttk.Frame(btns)
        statf.pack(side="right")
        self.stats_lbl = ttk.Label(statf, text="Ekuitas: Rp -  |  P/L hari ini: Rp 0",
                                   font=("Arial", 10, "bold"))
        self.stats_lbl.pack(anchor="e")
        self.float_lbl = tk.Label(statf, text="Floating P/L (jika kill now): Rp 0",
                                  font=("Arial", 10, "bold"), fg="#555")
        self.float_lbl.pack(anchor="e")

        body = ttk.PanedWindow(root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=4)

        left = ttk.Frame(body)
        ttk.Label(left, text="SINYAL TERATAS (skor 0-100)", font=("Arial", 10, "bold")).pack(anchor="w")
        cols = ("pair", "skor", "harga", "alasan")
        self.sig_tree = ttk.Treeview(left, columns=cols, show="headings", height=12)
        for c, w in zip(cols, (90, 50, 90, 430)):
            self.sig_tree.heading(c, text=c.upper())
            self.sig_tree.column(c, width=w, anchor="w")
        self.sig_tree.pack(fill="both", expand=True)
        ttk.Button(left, text="BELI PAIR TERPILIH (mode SIGNAL)",
                   command=self.manual_buy).pack(anchor="w", pady=4)
        body.add(left, weight=3)

        right = ttk.Frame(body)
        ttk.Label(right, text="POSISI TERBUKA (P/L floating)", font=("Arial", 10, "bold")).pack(anchor="w")
        pcols = ("pair", "entry", "qty", "skor", "float")
        self.pos_tree = ttk.Treeview(right, columns=pcols, show="headings", height=6)
        for c, w in zip(pcols, (80, 90, 90, 45, 110)):
            self.pos_tree.heading(c, text=c.upper())
            self.pos_tree.column(c, width=w, anchor="w")
        self.pos_tree.pack(fill="x")
        self.pos_tree.tag_configure("up", foreground="#1a8a1a")
        self.pos_tree.tag_configure("down", foreground="#c0392b")
        ttk.Label(right, text="LOG", font=("Arial", 10, "bold")).pack(anchor="w", pady=(8, 0))
        self.log_txt = tk.Text(right, height=14, state="disabled", font=("Consolas", 9))
        self.log_txt.pack(fill="both", expand=True)
        self.log_txt.tag_configure("win", foreground="#1a8a1a")
        self.log_txt.tag_configure("loss", foreground="#c0392b")
        self.log_txt.tag_configure("log", foreground="#222")
        body.add(right, weight=2)

        ttk.Label(root, foreground="#888", padding=(8, 0),
                  text=f"Log: {TRADE_LOG} | Tanpa fungsi withdraw. Mulai PAPER. "
                       f"Trading kripto berisiko — tidak ada jaminan profit."
                  ).pack(anchor="w")

    def _build_backtest(self, root):
        """Tab backtest: replay SignalEngine asli atas candle historis."""
        bar = ttk.Frame(root, padding=8)
        bar.pack(fill="x")
        ttk.Label(bar, text="BACKTEST — uji strategi atas data candle historis Tokocrypto/Binance",
                  font=("Arial", 11, "bold")).pack(anchor="w")
        ttk.Label(bar, foreground="#888",
                  text="Memakai SignalEngine yang sama persis dengan bot live, termasuk fee. "
                       "Hasil tetap estimasi — masa lalu bukan jaminan masa depan.").pack(anchor="w")

        cfgf = ttk.Frame(root, padding=8)
        cfgf.pack(fill="x")

        def bfield(label, key, default, width=10):
            f = ttk.Frame(cfgf)
            ttk.Label(f, text=label).pack(anchor="w")
            v = tk.StringVar(value=str(default))
            ttk.Entry(f, textvariable=v, width=width).pack()
            self.vars["bt_" + key] = v
            f.pack(side="left", padx=4)

        bfield("Jumlah pair top", "pairs", 30, 6)
        bfield("Timeframe", "tf", "5m", 6)
        bfield("Jumlah candle", "limit", 500, 8)
        bfield("Skor entry", "score", 60, 6)
        bfield("TP %", "tp", 2.0, 6)
        bfield("SL %", "sl", 1.0, 6)
        bfield("Time-stop (candle)", "tstop", 48, 8)
        bfield("Fee %/sisi", "fee", 0.15, 6)

        runf = ttk.Frame(root, padding=8)
        runf.pack(fill="x")
        self.bt_btn = ttk.Button(runf, text="▶ JALANKAN BACKTEST", command=self.run_backtest)
        self.bt_btn.pack(side="left")
        self.bt_status = ttk.Label(runf, text="Siap.", font=("Arial", 10))
        self.bt_status.pack(side="left", padx=12)

        res = ttk.Frame(root, padding=8)
        res.pack(fill="both", expand=True)
        self.bt_summary = tk.Label(res, text="", font=("Consolas", 11, "bold"),
                                   justify="left", anchor="w")
        self.bt_summary.pack(anchor="w", fill="x")
        cols = ("metrik", "nilai")
        self.bt_tree = ttk.Treeview(res, columns=cols, show="headings", height=14)
        self.bt_tree.heading("metrik", text="METRIK")
        self.bt_tree.heading("nilai", text="NILAI")
        self.bt_tree.column("metrik", width=320, anchor="w")
        self.bt_tree.column("nilai", width=200, anchor="w")
        self.bt_tree.pack(fill="both", expand=True, pady=6)

    # ---------- aksi ----------
    def _cfg(self):
        cfg = dict(DEFAULTS)
        for k in ("modal_idr", "daily_loss_limit_idr", "max_open", "usdt_idr",
                  "score_entry", "score_override", "max_size_idr",
                  "high_conv_score", "high_conv_pct"):
            cfg[k] = int(float(self.vars[k].get().replace(".", "").replace(",", ".")))
        for k in ("tp_pct", "sl_pct", "pos_size_pct", "time_stop_h"):
            cfg[k] = float(self.vars[k].get().replace(",", "."))
        cfg["top_n_pairs"] = max(10, min(100, int(float(
            self.vars["top_n_pairs"].get().replace(",", ".")))))
        cfg["kurs_auto"] = bool(self.kurs_auto_var.get())
        return cfg

    def start_bot(self):
        try:
            cfg = self._cfg()
        except ValueError:
            messagebox.showerror("Input salah", "Periksa kembali angka pengaturan.")
            return
        paper = self.paper_var.get() == "PAPER"
        if not paper:
            if not self.vars["api_key"].get() or not self.vars["api_secret"].get():
                messagebox.showerror("API", "Mode LIVE butuh API key & secret Tokocrypto.")
                return
            if not messagebox.askyesno(
                    "KONFIRMASI LIVE",
                    "Mode LIVE memakai UANG ASLI.\n\nPastikan:\n"
                    "1. Izin WITHDRAW pada API key sudah DINONAKTIFKAN\n"
                    "2. Anda sudah uji PAPER minimal beberapa hari\n"
                    "3. Modal adalah uang yang siap hilang\n\nLanjutkan?"):
                return
        self.bot = ZalfarkanaBot(cfg, self.q,
                                 mode_auto=self.mode_var.get() == "AUTO",
                                 paper=paper,
                                 api_key=self.vars["api_key"].get().strip(),
                                 api_secret=self.vars["api_secret"].get().strip(),
                                 cp_key=self.vars["cp_key"].get().strip())
        self.bot.start()
        self.start_btn["state"] = "disabled"
        self.stop_btn["state"] = "normal"
        self.kill_btn["state"] = "normal"

    def stop_bot(self):
        if self.bot:
            self.bot.stop_flag.set()
        self.start_btn["state"] = "normal"
        self.stop_btn["state"] = "disabled"
        self.kill_btn["state"] = "disabled"

    def kill(self):
        if self.bot and messagebox.askyesno("Kill Switch", "Tutup SEMUA posisi sekarang?"):
            self.bot.kill_flag.set()

    # ---------- BACKTEST (#1) ----------
    def run_backtest(self):
        if getattr(self, "_bt_running", False):
            return
        try:
            bt = {
                "pairs": int(float(self.vars["bt_pairs"].get())),
                "tf": self.vars["bt_tf"].get().strip(),
                "limit": int(float(self.vars["bt_limit"].get())),
                "score": float(self.vars["bt_score"].get()),
                "tp": float(self.vars["bt_tp"].get().replace(",", ".")),
                "sl": float(self.vars["bt_sl"].get().replace(",", ".")),
                "tstop": int(float(self.vars["bt_tstop"].get())),
                "fee": float(self.vars["bt_fee"].get().replace(",", ".")),
            }
        except ValueError:
            messagebox.showerror("Input salah", "Periksa angka di tab Backtest.")
            return
        self._bt_running = True
        self.bt_btn["state"] = "disabled"
        self.bt_tree.delete(*self.bt_tree.get_children())
        self.bt_summary["text"] = ""
        threading.Thread(target=self._backtest_worker, args=(bt,), daemon=True).start()

    def _backtest_worker(self, bt):
        """Replay SignalEngine asli atas candle historis. Entry saat skor
        >= ambang & semua filter lolos; exit di TP/SL/time-stop. Fee dihitung."""
        def status(msg):
            self.q.put(("bt_status", msg))
        try:
            md = MarketData()
            cfg = dict(DEFAULTS)
            cfg.update({"tp_pct": bt["tp"], "sl_pct": bt["sl"],
                        "time_stop_h": max(1, bt["tstop"] // 12), "fee_pct": bt["fee"]})
            engine = SignalEngine(md, cfg)
            status("Mengambil daftar pair...")
            pairs = md.top_pairs(bt["pairs"])
            fee = bt["fee"] / 100
            trades = []
            n = len(pairs)
            for i, (sym, base, vol, _) in enumerate(pairs):
                status(f"Backtest {i+1}/{n}: {sym}")
                try:
                    data = md._market_get("/api/v3/klines",
                                          {"symbol": sym, "interval": bt["tf"],
                                           "limit": bt["limit"]})
                except Exception:
                    continue
                opens = [float(k[1]) for k in data]
                highs = [float(k[2]) for k in data]
                lows = [float(k[3]) for k in data]
                closes = [float(k[4]) for k in data]
                vols = [float(k[5]) for k in data]
                # geser jendela: di tiap titik, evaluasi sinyal dgn data s/d titik itu
                pos = None  # (entry_idx, entry_price)
                j = 60
                while j < len(closes) - 1:
                    if pos is None:
                        sub_c = closes[:j+1]; sub_v = vols[:j+1]
                        sub_h = highs[:j+1]; sub_l = lows[:j+1]; sub_o = opens[:j+1]
                        sig = self._bt_score(engine, cfg, sym, base,
                                             sub_c, sub_v, sub_h, sub_l, sub_o)
                        if sig and sig >= bt["score"]:
                            pos = (j, closes[j]); entry_score = sig
                        j += 1
                    else:
                        ei, ep = pos
                        held = j - ei
                        hi = highs[j]; lo = lows[j]
                        tp_price = ep * (1 + bt["tp"] / 100)
                        sl_price = ep * (1 - bt["sl"] / 100)
                        exit_price = None; reason = None
                        # konservatif: cek SL dulu (asumsi terburuk dalam 1 candle)
                        if lo <= sl_price:
                            exit_price = sl_price; reason = "SL"
                        elif hi >= tp_price:
                            exit_price = tp_price; reason = "TP"
                        elif held >= bt["tstop"]:
                            exit_price = closes[j]; reason = "TIME"
                        if exit_price:
                            gross = (exit_price - ep) / ep
                            net = (1 + gross) * (1 - fee) * (1 - fee) - 1  # 2 sisi fee
                            trades.append({"pair": sym, "reason": reason,
                                           "net_pct": net * 100, "score": entry_score})
                            pos = None
                        j += 1
                time.sleep(0.1)
            self.q.put(("bt_done", {"trades": trades, "bt": bt}))
        except Exception as e:
            self.q.put(("bt_status", f"Error: {str(e)[:80]}"))
            self.q.put(("bt_done", None))

    def _bt_score(self, engine, cfg, sym, base, c, v, h, l, o):
        """Hitung skor pakai logika SignalEngine TANPA data eksternal
        (CoinGecko/F&G/berita tidak punya histori), murni teknikal+vol+candle.
        Mengembalikan skor atau None bila difilter."""
        import types
        # patch klines agar mengembalikan sub-window
        orig = engine.md.klines
        engine.md.klines = lambda *a, **k: (c, v, h, l, o)
        # patch data eksternal jadi netral utk backtest
        orig_cg = engine.md.coingecko_map; orig_fng = engine.md.fear_greed
        orig_tr = engine.md.trending; orig_news = engine.md.news_score
        engine.md.coingecko_map = lambda: {}
        engine.md.fear_greed = lambda: 50
        engine.md.trending = lambda: set()
        engine.md.news_score = lambda b, k: (0, "")
        try:
            sig = engine.score(sym, base, "")
            return sig["score"] if sig else None
        finally:
            engine.md.klines = orig
            engine.md.coingecko_map = orig_cg
            engine.md.fear_greed = orig_fng
            engine.md.trending = orig_tr
            engine.md.news_score = orig_news

    def _show_backtest(self, payload):
        self._bt_running = False
        self.bt_btn["state"] = "normal"
        if not payload:
            self.bt_status["text"] = "Backtest gagal."
            return
        trades = payload["trades"]; bt = payload["bt"]
        self.bt_tree.delete(*self.bt_tree.get_children())
        if not trades:
            self.bt_summary["text"] = "Tidak ada trade terbentuk dengan parameter ini."
            self.bt_status["text"] = "Selesai (0 trade)."
            return
        tp = [t for t in trades if t["reason"] == "TP"]
        sl = [t for t in trades if t["reason"] == "SL"]
        tm = [t for t in trades if t["reason"] == "TIME"]
        wins = [t for t in trades if t["net_pct"] > 0]
        total_net = sum(t["net_pct"] for t in trades)
        wr = len(wins) / len(trades) * 100
        tpr = len(tp) / (len(tp) + len(sl)) * 100 if (tp or sl) else 0
        avg = total_net / len(trades)
        # ekspektasi per trade dalam Rupiah (asumsi modal & size dr tab trading)
        color = "#1a8a1a" if total_net > 0 else "#c0392b"
        self.bt_summary["text"] = (
            f"{len(trades)} trade | Win rate {wr:.1f}% | TP-rate {tpr:.1f}% | "
            f"Net total {total_net:+.2f}% | Rata2/trade {avg:+.3f}%")
        self.bt_summary["fg"] = color
        rows = [
            ("Total trade", len(trades)),
            ("TP / SL / TIME", f"{len(tp)} / {len(sl)} / {len(tm)}"),
            ("Win rate (semua exit)", f"{wr:.1f}%"),
            ("TP-rate murni (TP vs SL)", f"{tpr:.1f}%"),
            ("Net total (semua trade)", f"{total_net:+.2f}%"),
            ("Rata-rata per trade", f"{avg:+.3f}%"),
            ("Break-even TP-rate", f"{bt['sl']/(bt['tp']+bt['sl'])*100:.1f}% (kasar, pra-fee)"),
            ("Rata2 TP", f"+{(sum(t['net_pct'] for t in tp)/len(tp)) if tp else 0:.3f}%"),
            ("Rata2 SL", f"{(sum(t['net_pct'] for t in sl)/len(sl)) if sl else 0:.3f}%"),
            ("Parameter", f"TP {bt['tp']}% SL {bt['sl']}% fee {bt['fee']}% skor≥{bt['score']:.0f}"),
        ]
        for m, val in rows:
            self.bt_tree.insert("", "end", values=(m, val))
        self.bt_status["text"] = "Selesai."


        if not self.bot:
            return
        sel = self.sig_tree.selection()
        if not sel:
            messagebox.showinfo("Pilih pair", "Pilih satu baris sinyal dulu.")
            return
        pair = self.sig_tree.item(sel[0])["values"][0]
        try:
            msize = int(float(self.vars["manual_size_idr"].get().replace(".", "")))
        except ValueError:
            msize = 0
        for sig in self.bot.last_signals:
            if sig["symbol"] == pair:
                self.bot.manual_buy_q.put((sig, msize))
                return

    # ---------- update UI ----------
    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind in ("log", "win", "loss"):
                    self.log_txt["state"] = "normal"
                    self.log_txt.insert("end", payload + "\n", kind)
                    self.log_txt.see("end")
                    self.log_txt["state"] = "disabled"
                elif kind == "signals":
                    self.sig_tree.delete(*self.sig_tree.get_children())
                    for s in payload:
                        self.sig_tree.insert("", "end", values=(
                            s["symbol"], s["score"], f"{s['price']:.6g}",
                            "; ".join(s["reasons"][:5])))
                elif kind == "positions" and self.bot:
                    self._refresh_positions()
                elif kind == "float" and self.bot:
                    self._refresh_positions()
                    col = "#1a8a1a" if payload >= 0 else "#c0392b"
                    sign = "+" if payload >= 0 else ""
                    self.float_lbl["text"] = (
                        f"Floating P/L (jika kill now): {sign}Rp {payload:,.0f}"
                        .replace(",", "."))
                    self.float_lbl["fg"] = col
                elif kind == "stats" and self.bot:
                    pl = self.bot.risk.realized_pl_idr
                    eq = self.bot.equity_idr()
                    wr = (self.bot.win_count / self.bot.closed_count * 100
                          if self.bot.closed_count else 0)
                    self.stats_lbl["text"] = (
                        f"Ekuitas: Rp {eq:,.0f}  |  P/L hari ini: Rp {pl:,.0f}  |  "
                        f"WR {wr:.0f}% ({self.bot.win_count}/{self.bot.closed_count})"
                        .replace(",", "."))
                elif kind == "bt_status":
                    self.bt_status["text"] = payload
                elif kind == "bt_done":
                    self._show_backtest(payload)
        except queue.Empty:
            pass
        self.after(300, self._poll)

    def _refresh_positions(self):
        if not self.bot:
            return
        self.pos_tree.delete(*self.pos_tree.get_children())
        for p in list(self.bot.positions.values()):
            try:
                cur = self.bot.md.price(p.symbol)
                fl = self.bot.idr(p.qty * cur * (1 - self.bot.cfg["fee_pct"] / 100)
                                  - p.usdt_in)
                sign = "+" if fl >= 0 else ""
                ftxt = f"{sign}Rp {fl:,.0f}".replace(",", ".")
                tag = "up" if fl >= 0 else "down"
            except Exception:
                ftxt, tag = "-", ""
            self.pos_tree.insert("", "end", tags=(tag,), values=(
                p.symbol, f"{p.entry:.6g}", f"{p.qty:.6f}", p.score, ftxt))


if __name__ == "__main__":
    App().mainloop()
