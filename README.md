# Trade-Texter

Automated swing-trade signal bot and live dashboard for US and Indian (NSE) stocks.
Scores your portfolio, surfaces options ideas, and sends actionable alerts to Telegram.

**Live dashboard:** https://trade-texter.onrender.com (Render free tier — first load may take ~50s to wake)

## What it does

- **Live dashboard** — unified browser UI (`dashboard.html`) with tabs for US signals, India (NSE) signals, Top 5 Picks, an **Options tab** (call/put strike ideas from live option chains), penny-stock momentum plays, charts, and backtests
- **Daily morning alert** — full portfolio scan with BUY/SELL/HOLD signals, price targets, ATR stop losses, outside picks, and penny-stock momentum
- **Intraday scanner (market hours)** — fires only when a signal *changes*, so alerts are actionable, not noisy
- **Telegram delivery** — morning report and change alerts pushed to your chat

## Options tab

For any US ticker with an active BUY/SELL signal, the backend pulls a live yfinance
option chain and suggests a call (near support) or put (near resistance) at the nearest
expiry and ~3–4 weeks out. When no ticker has an active signal, the tab falls back to
**directional-lean** ideas on a set of liquid large-caps (AAPL, MSFT, NVDA, … SPY, QQQ),
marked **WATCH**, so it always shows real option-chain quotes. Educational only — not a
trade recommendation.

## Setup (local)

### 1. Install dependencies
```bash
pip3 install -r requirements.txt   # yfinance pandas numpy pytz requests curl_cffi
```

### 2. Add your Telegram credentials
Credentials are read from environment variables (preferred) or `config.json`:
```bash
export TELEGRAM_BOT_TOKEN="your_bot_token"   # from @BotFather → /newbot
export TELEGRAM_CHAT_ID="your_chat_id"       # /start your bot, then visit
                                             # https://api.telegram.org/bot<TOKEN>/getUpdates
```

### 3. Customise your portfolio
Edit `US_PORTFOLIO` / `INDIA_PORTFOLIO` in the source, or set them via the dashboard's
config (saved to `config.json`).

### 4. Start the dashboard
```bash
python3 server.py
# Open http://localhost:8080/dashboard.html
```

### 5. Run alerts manually to test
```bash
python3 telegram_alert.py     # full morning report
python3 intraday_scanner.py   # one intraday check
python3 india_alert.py        # India (NSE) scan
```

### 6. Auto-schedule on macOS (optional)
```bash
cp com.mclean.tradealerts.plist ~/Library/LaunchAgents/
cp com.mclean.intraday.plist    ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.mclean.tradealerts.plist
launchctl load ~/Library/LaunchAgents/com.mclean.intraday.plist
```

## Deploy (Render)

The repo auto-deploys to Render on every push to `main`.

- `Procfile` → `web: python server.py`
- `render.yaml` → service config; set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as env vars
- Server binds to `$PORT` (defaults to 8080 locally)

```bash
git add <changed files> && git commit -m "..." && git push origin main
```

## Signal engine

Each stock is scored across several factors:

| Factor | Bullish | Bearish |
|---|---|---|
| RSI | < 45 oversold | > 60 overbought |
| MACD | Line above signal | Line below signal |
| Trend | Above 50+200 EMA | Below 50 EMA |
| Support/Resistance | Near 60-day support | Near 60-day resistance |
| Volume | 1.2x above avg | Below 0.5x avg |
| AI trend | Upward forecast | Downward forecast |

Score ≥ 4 → **STRONG BUY** · ≥ 2 → **BUY** · ≤ -2 → **SELL** · ≤ -4 → **STRONG SELL**

When ADX < 20 (range-bound / no real trend), strong signals are dampened toward HOLD to
avoid firing into chop. Stop losses are ATR-based (2× 14-day Average True Range), not fixed
percentages. BUY signals are suppressed automatically when SPY drops sharply on the day.

## Key API routes (`server.py`)

`/api/signals` · `/api/options` · `/api/top-picks` · `/api/quotes` · `/api/chart` ·
`/api/backtest` · `/api/custom-signals` · `/api/config` · `/api/scan` · `/api/symbol-lookup`

## Files

| File | Purpose |
|---|---|
| `server.py` | API server + scanners + dashboard backend (US, India, options, top picks, backtest) |
| `dashboard.html` | Unified browser dashboard (all tabs) |
| `telegram_alert.py` | Morning alert engine |
| `intraday_scanner.py` | Change-only intraday alerts |
| `india_alert.py` | India (NSE) scan + alerts |
| `Procfile`, `render.yaml` | Render deploy config |
| `com.mclean.tradealerts.plist` | macOS LaunchAgent — daily |
| `com.mclean.intraday.plist` | macOS LaunchAgent — intraday |

## Logs

- `tradealerts.log` — daily alert run history
- `intraday.log` — intraday scanner history
- `server.log` — dashboard / API server log

---

*Not financial advice. Options can expire worthless. Use at your own risk.*
