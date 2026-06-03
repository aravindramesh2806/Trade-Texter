#!/usr/bin/env python3
"""
McLean Trade Bot — India Alert Script (NSE)
Runs automatically at 8:45 AM IST weekdays (before 9:15 AM market open).

Covers NSE-listed stocks via yfinance (.NS suffix).
Same signal engine as the US bot: RSI, MACD, EMA, AI trend, ATR stop loss.

SETUP:
  1. Set your Telegram token + chat ID in config.json (or edit directly below)
  2. Schedule via cron (IST = UTC+5:30):
       crontab -e
       15 3 * * 1-5 cd /path/to/project && python3 india_alert.py
     (3:15 UTC = 8:45 AM IST)
  3. Or run manually:
       python3 india_alert.py
"""
import urllib.request, urllib.parse, sys, os, time, logging, json
from datetime import datetime, timedelta

# ── Config ────────────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))
_CFG_FILE = os.path.join(_DIR, "config.json")

def load_config():
    if os.path.exists(_CFG_FILE):
        try:
            return json.load(open(_CFG_FILE))
        except Exception:
            pass
    return {}

_cfg = load_config()

TELEGRAM_TOKEN   = _cfg.get("telegram_token",   "8955387419:AAHEmmHoibcYkv2MRcFElzX__4TOrP55PjQ")
TELEGRAM_CHAT_ID = _cfg.get("telegram_chat_id", "1578063059")

# NSE portfolio — your personal holdings (edit via dashboard or config.json)
PORTFOLIO = _cfg.get("india_portfolio", [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
    "WIPRO", "BAJFINANCE", "TITAN", "ASIANPAINT", "MARUTI",
])

# Broader NSE watchlist — top NIFTY 50 + mid-cap momentum names
WATCHLIST = [
    # NIFTY 50 heavyweights
    "HINDUNILVR","NESTLEIND","BAJAJFINSV","KOTAKBANK","LTIM",
    "SUNPHARMA","DRREDDY","CIPLA","DIVISLAB","APOLLOHOSP",
    "ADANIPORTS","ADANIENT","POWERGRID","NTPC","COALINDIA",
    "ONGC","BPCL","IOC","GAIL","TATAMOTORS",
    "TATACONSUM","ITC","BRITANNIA","DABUR","MARICO",
    "JSWSTEEL","TATASTEEL","HINDALCO","VEDL","SAIL",
    "ULTRACEMCO","GRASIM","ACC","AMBUJACEM","SHREECEM",
    "AXISBANK","INDUSINDBK","FEDERALBNK","IDFCFIRSTB","BANDHANBNK",
    # Mid-cap momentum
    "PERSISTENT","LTTS","COFORGE","MPHASIS","KPITTECH",
    "ZOMATO","NYKAA","PAYTM","POLICYBZR","DELHIVERY",
]
TOP_WATCHLIST = 3

# Market filter: use NIFTYBEES as SPY equivalent
NIFTY_PROXY    = "NIFTYBEES.NS"
BEAR_THRESHOLD = -1.5   # % — suppress BUY signals if NIFTY drops this much

ATR_MULTIPLIER       = 2.0
VOLUME_CONFIRM_RATIO = 1.2
MAX_DATA_AGE_DAYS    = 5
# ─────────────────────────────────────────────────────────────────

LOG_FILE = os.path.join(_DIR, "india_alerts.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("india")


def install_deps():
    for pkg in ["yfinance", "pandas", "numpy"]:
        try:
            __import__(pkg)
        except ImportError:
            log.info(f"Installing {pkg}...")
            os.system(f"{sys.executable} -m pip install {pkg} -q --break-system-packages")

install_deps()

import yfinance as yf
import pandas as pd
import numpy as np


# ── Helpers ───────────────────────────────────────────────────────

def ns(sym: str) -> str:
    """Append .NS suffix for NSE lookup if not already present."""
    return sym if sym.endswith(".NS") else sym + ".NS"

def fmt_inr(val) -> str:
    """Format a number as Indian Rupees (₹1,23,456.78)."""
    try:
        val = float(val)
        if val >= 1e7:
            return f"₹{val/1e7:.2f}Cr"
        if val >= 1e5:
            return f"₹{val/1e5:.2f}L"
        return f"₹{val:,.2f}"
    except Exception:
        return "₹—"

def is_valid(val):
    try:
        return np.isfinite(float(val))
    except Exception:
        return False

def safe_float(val, default=None):
    try:
        f = float(val)
        return f if np.isfinite(f) else default
    except Exception:
        return default


# ── Telegram ─────────────────────────────────────────────────────

def send_telegram(message: str, retries: int = 3) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for attempt in range(1, retries + 1):
        try:
            data = urllib.parse.urlencode({
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       message,
                "parse_mode": "HTML",
            }).encode()
            urllib.request.urlopen(url, data=data, timeout=15)
            log.info("Telegram sent successfully.")
            return True
        except Exception as e:
            wait = 2 ** attempt
            log.warning(f"Telegram attempt {attempt}/{retries} failed: {e}. Retry in {wait}s")
            if attempt < retries:
                time.sleep(wait)
    log.error("All Telegram retries failed.")
    return False


# ── Indicators ────────────────────────────────────────────────────

def calc_rsi(series, period=14):
    if len(series) < period + 1:
        return None
    delta = series.diff().dropna()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    last_loss = safe_float(loss.iloc[-1])
    last_gain = safe_float(gain.iloc[-1])
    if last_loss is None or last_gain is None:
        return None
    if last_loss == 0:
        return 100.0
    rs  = last_gain / last_loss
    return safe_float(100 - 100 / (1 + rs))

def calc_macd(series):
    if len(series) < 35:
        return None, None
    ema12  = series.ewm(span=12, adjust=False).mean()
    ema26  = series.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return safe_float(macd.iloc[-1]), safe_float(signal.iloc[-1])

def calc_ai_trend(close, lookback=30, forecast_days=5) -> int:
    """Linear regression + momentum trend predictor. Returns +1, 0, or -1."""
    try:
        if len(close) < lookback + forecast_days:
            return 0
        prices = np.array(close.tail(lookback), dtype=float)
        if not np.all(np.isfinite(prices)):
            return 0
        x  = np.arange(lookback)
        xm = x - x.mean()
        slope     = (xm @ prices) / (xm @ xm)
        slope_pct = slope / prices.mean() * 100
        momentum_pct = (prices[-1] / prices[-forecast_days] - 1) * 100
        if slope_pct > 0.05 and momentum_pct > 0:
            return 1
        elif slope_pct < -0.05 and momentum_pct < 0:
            return -1
        return 0
    except Exception:
        return 0

def calc_atr(hist, period=14):
    if len(hist) < period + 1:
        return None
    high, low, close = hist["High"], hist["Low"], hist["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return safe_float(tr.rolling(period).mean().iloc[-1])


# ── Market mood (NIFTY proxy) ─────────────────────────────────────

def get_market_mood():
    try:
        t    = yf.Ticker(NIFTY_PROXY)
        hist = t.history(period="5d")
        if hist is None or len(hist) < 2:
            log.warning("NIFTY proxy: could not fetch market mood")
            return None, False
        price = safe_float(hist["Close"].iloc[-1])
        prev  = safe_float(hist["Close"].iloc[-2])
        if price is None or prev is None or prev == 0:
            return None, False
        pct = (price - prev) / prev * 100
        is_bearish = pct <= BEAR_THRESHOLD
        log.info(f"NIFTY: {pct:+.2f}%  {'BEARISH — BUY suppressed' if is_bearish else 'OK'}")
        return round(pct, 2), is_bearish
    except Exception as e:
        log.warning(f"NIFTY mood check failed: {e} — defaulting to neutral")
        return None, False


# ── Signal engine ─────────────────────────────────────────────────

def get_signal(sym: str):
    ticker_sym = ns(sym)
    try:
        t    = yf.Ticker(ticker_sym)
        hist = t.history(period="1y")

        if hist is None or hist.empty or len(hist) < 30:
            log.warning(f"{sym}: insufficient data")
            return None

        last_date = hist.index[-1]
        if hasattr(last_date, "tzinfo") and last_date.tzinfo is not None:
            last_date = last_date.replace(tzinfo=None)
        if (datetime.now() - last_date).days > MAX_DATA_AGE_DAYS:
            log.warning(f"{sym}: stale data — skipping")
            return None

        close = hist["Close"].dropna()
        if len(close) < 30:
            return None

        price = safe_float(close.iloc[-1])
        prev  = safe_float(close.iloc[-2])
        if price is None or prev is None or prev == 0:
            return None

        pct = (price - prev) / prev * 100

        rsi          = calc_rsi(close)
        macd, signal = calc_macd(close)
        ema50        = safe_float(close.ewm(span=50,  adjust=False).mean().iloc[-1])
        ema200       = safe_float(close.ewm(span=200, adjust=False).mean().iloc[-1])
        support      = safe_float(hist["Low"].tail(60).min())
        resistance   = safe_float(hist["High"].tail(60).max())
        atr          = calc_atr(hist)

        avg_vol  = safe_float(hist["Volume"].tail(20).mean())
        curr_vol = safe_float(hist["Volume"].iloc[-1])
        vol_ratio = (curr_vol / avg_vol) if (avg_vol and avg_vol > 0 and curr_vol) else None

        if any(v is None for v in [rsi, ema50, ema200, support, resistance]):
            log.warning(f"{sym}: indicator NaN — skipping")
            return None
        if resistance <= support or support <= 0:
            return None

        # ── Scoring ───────────────────────────────────────────────
        score, reasons = 0, []

        if rsi < 30:    score += 2; reasons.append(f"RSI {rsi:.0f} oversold")
        elif rsi < 45:  score += 1; reasons.append(f"RSI {rsi:.0f} neutral-low")
        elif rsi > 70:  score -= 2; reasons.append(f"RSI {rsi:.0f} overbought")
        elif rsi > 60:  score -= 1; reasons.append(f"RSI {rsi:.0f} elevated")

        if macd is not None and signal is not None:
            if macd > signal: score += 1; reasons.append("MACD bullish")
            else:             score -= 1; reasons.append("MACD bearish")

        if price > ema50 and ema50 > ema200:
            score += 2; reasons.append("above 50+200 EMA (strong uptrend)")
        elif price > ema50:
            score += 1; reasons.append("above 50 EMA")
        elif price < ema50:
            score -= 1; reasons.append("below 50 EMA")

        near_support    = (price - support)    / price < 0.03
        near_resistance = (resistance - price) / price < 0.03
        if near_support:    score += 1; reasons.append("near support")
        if near_resistance: score -= 1; reasons.append("near resistance")

        if vol_ratio is not None:
            if vol_ratio >= VOLUME_CONFIRM_RATIO:
                score += 1; reasons.append(f"volume {vol_ratio:.1f}x above avg")
            elif vol_ratio < 0.5:
                score -= 1; reasons.append(f"volume {vol_ratio:.1f}x — thin")

        ai_signal = calc_ai_trend(close)
        if ai_signal == 1:  score += 1; reasons.append("AI trend: upward forecast")
        elif ai_signal == -1: score -= 1; reasons.append("AI trend: downward forecast")

        # ── Label ─────────────────────────────────────────────────
        if   score >= 4:  label = "STRONG BUY"
        elif score >= 2:  label = "BUY"
        elif score <= -4: label = "STRONG SELL"
        elif score <= -2: label = "SELL"
        else:             label = "HOLD"

        # ── Why ───────────────────────────────────────────────────
        why_parts = []
        if "BUY" in label:
            if rsi < 35:     why_parts.append(f"RSI {rsi:.0f} — deeply oversold")
            elif rsi < 45:   why_parts.append(f"RSI {rsi:.0f} — pulled back to good levels")
            if near_support: why_parts.append(f"sitting on support {fmt_inr(support)}")
            if macd is not None and signal is not None and macd > signal:
                why_parts.append("MACD turning bullish")
            if price > ema50: why_parts.append("above 50-day MA — uptrend intact")
            if pct < -2:     why_parts.append(f"dipped {pct:.1f}% today")
            if ai_signal == 1: why_parts.append("AI trend confirms upward momentum")
        elif "SELL" in label:
            if rsi > 70:        why_parts.append(f"RSI {rsi:.0f} — overbought")
            if near_resistance: why_parts.append(f"hitting resistance {fmt_inr(resistance)}")
            if pct > 5:         why_parts.append(f"up {pct:.1f}% today — lock in gains")
            if ai_signal == -1: why_parts.append("AI trend confirms downward pressure")
        else:
            why_parts = reasons[:2]

        why = " · ".join(why_parts[:2]) if why_parts else ", ".join(reasons[:2])
        if not why:
            why = f"Score {score}/6"

        if atr and atr > 0:
            atr_stop  = round(price - ATR_MULTIPLIER * atr, 2)
            stop_loss = max(atr_stop, round(support * 0.98, 2))
        else:
            stop_loss = round(support * 0.98, 2)

        return {
            "symbol":     sym,
            "price":      round(price, 2),
            "pct":        round(pct, 2),
            "rsi":        round(rsi, 1),
            "score":      score,
            "label":      label,
            "why":        why,
            "support":    round(support, 2),
            "resistance": round(resistance, 2),
            "stop_loss":  stop_loss,
            "atr":        round(atr, 2) if atr else None,
            "vol_ratio":  round(vol_ratio, 1) if vol_ratio else None,
            "ai_signal":  ai_signal,
        }

    except Exception as e:
        log.error(f"{sym}: unexpected error — {e}")
        return None


# ── Watchlist scanner ─────────────────────────────────────────────

def scan_watchlist():
    picks = []
    log.info(f"Scanning {len(WATCHLIST)} NSE watchlist stocks...")
    for sym in WATCHLIST:
        try:
            result = get_signal(sym)
            if result and result["score"] >= 2:
                picks.append(result)
        except Exception as e:
            log.warning(f"Watchlist {sym}: {e}")
    picks.sort(key=lambda x: x["score"], reverse=True)
    return picks[:TOP_WATCHLIST]


# ── Message builder ───────────────────────────────────────────────

def build_message(signals, watchlist_picks=None, nifty_pct=None, market_bearish=False):
    now   = datetime.now().strftime("%a, %b %-d %Y")
    buys  = [s for s in signals if "BUY"  in s.get("label", "")]
    sells = [s for s in signals if "SELL" in s.get("label", "")]
    holds = [s for s in signals if s.get("label") == "HOLD"]
    summary = f"{len(buys)} BUY  {len(holds)} HOLD  {len(sells)} SELL"

    best = max(
        (s for s in signals if "BUY" in s.get("label", "")),
        key=lambda x: x["score"],
        default=None
    )

    nifty_line = ""
    if nifty_pct is not None:
        d = f"+{nifty_pct}%" if nifty_pct >= 0 else f"{nifty_pct}%"
        nifty_line = f"  |  NIFTY {d}"
        if market_bearish:
            nifty_line += "  [BUY SIGNALS SUPPRESSED]"

    lines = [
        f"<b>McLean Trade Bot — India (NSE)</b> · {now} · 8:45 AM",
        f"Signals: {summary}{nifty_line}",
    ]

    if best:
        profit_pct = round((best["resistance"] - best["price"]) / best["price"] * 100, 1)
        lines += [
            "",
            "<b>── BEST PICK TODAY ──────────────</b>",
            f'<b>{best["symbol"]}</b>',
            f'  Price:  {fmt_inr(best["price"])}',
            f'  Target: {fmt_inr(best["resistance"])}  (+{profit_pct}%)',
            f'  Stop:   {fmt_inr(best["stop_loss"])}  (ATR-based)',
            f'  Why:    {best["why"]}',
        ]

    action_stocks = buys + sells
    if action_stocks:
        lines += ["", "<b>── ACT NOW ──────────────────────</b>"]
        for s in action_stocks:
            day_chg = f'+{s["pct"]}%' if s["pct"] >= 0 else f'{s["pct"]}%'
            is_buy  = "BUY" in s["label"]
            lines.append("")
            lines.append(f'<b>{s["symbol"]}</b>  {fmt_inr(s["price"])}  ({day_chg})  <b>{s["label"]}</b>')
            lines.append(f'  Why:  {s["why"]}')
            if is_buy:
                pct = round((s["resistance"] - s["price"]) / s["price"] * 100, 1)
                lines.append(f'  Target: {fmt_inr(s["resistance"])}  (+{pct}%)')
                lines.append(f'  Stop:   {fmt_inr(s["stop_loss"])}  (ATR-based)')
            else:
                lines.append(f'  Take profit near {fmt_inr(s["resistance"])}')
                lines.append(f'  Risk: could fall to support {fmt_inr(s["support"])}')

    lines += ["", "<b>── PORTFOLIO ────────────────────</b>",
              "<code>TICKER       PRICE       DAY     SIGNAL</code>"]
    for s in signals:
        day_chg = f'+{s["pct"]}%' if s["pct"] >= 0 else f'{s["pct"]}%'
        warn    = " *" if s.get("rsi", 0) > 72 else ""
        lines.append(
            f'<code>{s["symbol"]:<12}  {fmt_inr(s["price"]):<10}  {day_chg:<7}  {s["label"]}{warn}</code>'
        )

    if watchlist_picks:
        lines += ["", "<b>── 3 NSE STOCKS TO WATCH ────────</b>",
                  "<i>(outside your portfolio)</i>"]
        for s in watchlist_picks:
            pct = round((s["resistance"] - s["price"]) / s["price"] * 100, 1)
            day = f'+{s["pct"]}%' if s["pct"] >= 0 else f'{s["pct"]}%'
            lines += [
                "",
                f'<b>{s["symbol"]}</b>  {fmt_inr(s["price"])}  ({day})',
                f'  Target: {fmt_inr(s["resistance"])}  (+{pct}%)   Stop: {fmt_inr(s["stop_loss"])}',
                f'  Why:    <i>{s["why"]}</i>',
            ]

    lines += ["", "<i>McLean Trade Bot · NSE India · Not financial advice</i>"]
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=== McLean Trade Bot (India/NSE) starting ===")

    nifty_pct, market_bearish = get_market_mood()

    log.info(f"Fetching portfolio: {', '.join(PORTFOLIO)}")
    signals = []
    for sym in PORTFOLIO:
        result = get_signal(sym)
        if result:
            if market_bearish and "BUY" in result["label"]:
                log.info(f"  {sym}: {result['label']} downgraded to HOLD (bearish NIFTY)")
                result["label"] = "HOLD"
                result["why"]   = f"Signal suppressed — NIFTY down {nifty_pct}%"
            signals.append(result)
            log.info(f"  {sym}: {result['label']}  score={result['score']}  rsi={result['rsi']}")
        else:
            log.warning(f"  {sym}: no valid signal")

    if not signals:
        log.error("No valid signals — aborting.")
        sys.exit(1)

    watchlist_picks = scan_watchlist()
    if market_bearish:
        watchlist_picks = [s for s in watchlist_picks if "BUY" not in s.get("label", "")]
    log.info(f"Watchlist picks: {[s['symbol'] for s in watchlist_picks]}")

    # Standalone BUY alert
    buys = [s for s in signals if "BUY" in s.get("label", "")]
    if buys:
        buy_lines = ["BUY ALERT — NSE Opportunity Now", ""]
        for s in buys:
            day_chg    = f'+{s["pct"]}%' if s["pct"] >= 0 else f'{s["pct"]}%'
            profit_pct = round((s["resistance"] - s["price"]) / s["price"] * 100, 1)
            buy_lines += [
                f'<b>{s["symbol"]}</b>  {fmt_inr(s["price"])}  ({day_chg})  <b>{s["label"]}</b>',
                f'  {s["why"]}',
                f'  Target: {fmt_inr(s["resistance"])}  (+{profit_pct}%)',
                f'  Stop:   {fmt_inr(s["stop_loss"])}',
                "",
            ]
        buy_lines.append("<i>McLean Trade Bot · Not financial advice</i>")
        send_telegram("\n".join(buy_lines))
        time.sleep(1)

    # Standalone SELL alert
    sells = [s for s in signals if "SELL" in s.get("label", "")]
    if sells:
        sell_lines = ["SELL ALERT — NSE Act Now", ""]
        for s in sells:
            day_chg = f'+{s["pct"]}%' if s["pct"] >= 0 else f'{s["pct"]}%'
            sell_lines += [
                f'<b>{s["symbol"]}</b>  {fmt_inr(s["price"])}  ({day_chg})  <b>{s["label"]}</b>',
                f'  {s["why"]}',
                f'  Sell near: {fmt_inr(s["resistance"])}',
                f'  Stop at:   {fmt_inr(s["stop_loss"])}',
                "",
            ]
        sell_lines.append("<i>McLean Trade Bot · Not financial advice</i>")
        send_telegram("\n".join(sell_lines))
        time.sleep(1)

    message = build_message(signals, watchlist_picks, nifty_pct, market_bearish)
    log.info("Sending main India report to Telegram...")
    print(message)

    success = send_telegram(message)
    if not success:
        log.error("Alert delivery failed after all retries.")
        sys.exit(1)

    log.info("=== McLean Trade Bot (India/NSE) done ===")
