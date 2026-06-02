# McLean Trade Bot

Automated swing trade signal bot for US stocks. Runs on your Mac, sends alerts to Telegram.

## What it does

- **7AM daily alert** — full portfolio scan with BUY/SELL/HOLD signals, price targets, stop losses, 3 outside picks, and penny stock momentum plays
- **Every 30 min (market hours)** — intraday scanner fires only when a signal *changes* — no noise, only actionable updates
- **Live dashboard** — browser-based stock dashboard at `localhost:8080`

## Setup

### 1. Install dependencies
```bash
pip3 install yfinance pandas numpy pytz
```

### 2. Add your Telegram credentials
In `telegram_alert.py`, `intraday_scanner.py`, and `server.py`, replace:
```python
TELEGRAM_TOKEN   = "YOUR_BOT_TOKEN_HERE"   # From @BotFather on Telegram
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID_HERE"     # From https://api.telegram.org/bot<TOKEN>/getUpdates
```

To get your token: open Telegram → search @BotFather → `/newbot`  
To get your chat ID: send `/start` to your bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates`

### 3. Customise your portfolio
Edit the `PORTFOLIO` list in `telegram_alert.py` and `intraday_scanner.py`.

### 4. Install LaunchAgents (macOS auto-scheduling)
```bash
cp com.mclean.tradealerts.plist ~/Library/LaunchAgents/
cp com.mclean.intraday.plist    ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.mclean.tradealerts.plist
launchctl load ~/Library/LaunchAgents/com.mclean.intraday.plist
```

### 5. Run manually to test
```bash
python3 telegram_alert.py     # sends full morning report
python3 intraday_scanner.py   # runs one intraday check
```

### 6. Start the live dashboard
```bash
python3 server.py
# Open http://localhost:8080/live-stocks.html
```

## Signal engine

Each stock is scored across 5 factors:

| Factor | Bullish | Bearish |
|---|---|---|
| RSI | < 45 oversold | > 60 overbought |
| MACD | Line above signal | Line below signal |
| Trend | Above 50+200 EMA | Below 50 EMA |
| Support/Resistance | Near 60-day support | Near 60-day resistance |
| Volume | 1.2x above avg | Below 0.5x avg |

Score ≥ 4 → **STRONG BUY** · ≥ 2 → **BUY** · ≤ -2 → **SELL** · ≤ -4 → **STRONG SELL**

Stop losses are ATR-based (2× 14-day Average True Range), not fixed percentages.  
BUY signals are suppressed automatically if SPY drops more than 1.5% on the day.

## Files

| File | Purpose |
|---|---|
| `telegram_alert.py` | Morning alert engine (runs at 7AM) |
| `intraday_scanner.py` | Change-only intraday alerts (every 30 min) |
| `server.py` | Local API server + dashboard backend |
| `live-stocks.html` | Browser dashboard |
| `com.mclean.tradealerts.plist` | macOS LaunchAgent — 7AM daily |
| `com.mclean.intraday.plist` | macOS LaunchAgent — every 30 min |

## Logs

- `tradealerts.log` — daily alert run history  
- `intraday.log` — intraday scanner history  
- `intraday_state.json` — today's signal state (auto-generated, resets daily)

---

*Not financial advice. Use at your own risk.*
