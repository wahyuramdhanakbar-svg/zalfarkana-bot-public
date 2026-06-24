# Zalfarkana Trading Bot 🤖💰

**Zalfarkana** — Crypto auto-trading bot untuk Binance Futures (PAPER mode). 
Bot ini jalan di VPS Linux 24/7, dengan monitoring via Telegram.

## Fitur

- 🚀 Auto-trading Binance Futures (PAPER mode)
- 📊 Score-based entry dengan override mekanik
- 🛡️ Stop Loss (0.6%) & Take Profit (1.5%)
- ⏰ Time stop 3 jam per posisi
- 📈 Daily loss limit
- 🤖 Cooldown sistem setelah loss berturut-turut
- 📱 Monitoring via Telegram
- 📉 Daily report otomatis
- 🔄 High conviction mode

## File-File Utama

| File | Fungsi |
|------|--------|
| `zalfarkana.py` | Bot utama — semua logika trading (1.544 baris) |
| `zalfarkana_vps.py` | Wrapper headless untuk jalan di VPS |
| `vps_config.example.json` | Contoh config — **copy ke `vps_config.json`** |
| `auto_analyzer.py` | Analisa otomatis market |
| `daily_report.py` | Report harian via Telegram |
| `monitor_telegram.py` | Monitoring Telegram real-time |

## Setup

1. **Clone repo:**
   ```bash
   git clone https://github.com/wahyuramdhanakbar-svg/zalfarkana-trading-bot.git
   cd zalfarkana-trading-bot
   ```

2. **Buat config:**
   ```bash
   cp vps_config.example.json vps_config.json
   # lalu edit vps_config.json (isi API key dll)
   ```

3. **Jalankan (PAPER mode dulu!):**
   ```bash
   python3 zalfarkana_vps.py
   ```

## ⚠️ PENTING

- **JANGAN commit `vps_config.json`** — sudah di `.gitignore`
- **JANGAN commit `.env` atau `.telegram_*`** — berisi token sensitif!
- **Pastikan `paper: true`** di config sampai benar-benar siap LIVE

---

*Dibuat untuk kebutuhan pribadi — use at your own risk!*