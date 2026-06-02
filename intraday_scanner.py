#!/usr/bin/env python3
"""
McLean Trade Bot — Intraday Scanner
Runs every 30 min via LaunchAgent during market hours.

Behaviour:
  - First scan of the day: silent (7AM daily alert already sent full report)
  - Subsequent scans: only fires Telegram when a signal CHANGES
      e.g. HOLD -> BUY, BUY -> SELL, or a new intraday event appears
  - State is saved to intraday_state.json between runs
  - Duplicate alerts for the same signal on the same day are suppressed
"""
import urllib.request, urllib.parse, sys, os, json, time, logging
from datetime import datetime, date
import pytz

DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(DIR, "intraday_state.json")
LOG_FILE   = os.path.join(DIR, "intraday.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("intraday")

# ── Config ───────────────────────────────────────────────────────
TELEGRAM_TOKEN   = "YOUR_BOT_TOKEN_HERE"  # Get from @BotFather on Telegram
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID_HERE"    # Send /start to your bot, then visit: https://api.telegram.org/bot<TOKEN>/getUpdates
PORTFOLIO        = ["AAPL", "GOOGL", "PLTR", "VOO", "NVDA", "AMD", "AMZN", "CRM"]

RSI_OVERSOLD   = 35
RSI_OVERBOUGHT = 72
INTRADAY_DIP   = -3.0   # % from open = bounce candidate
INTRADAY_SURGE =  3.0   # % from open + volume = breakout
VOLUME_SPIKE   =  2.5   # x above avg daily volume
# ─────────────────────────────────────────────────────────────────


def is_market_hours():
    et  = pytz.timezone("America/New_York")
    now = datetime.now(et)
    if now.weekday() >= 5:
        return False
    t = now.time()
    from datetime import time as dtime
    return dtime(9, 25) <= t <= dtime(16, 5)


def send_telegram(message, retries=3):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for attempt in range(1, retries + 1):
        try:
            data = urllib.parse.urlencode({
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       message,
                "parse_mode": "HTML"
            }).encode()
            urllib.request.urlopen(url, data=data, timeout=15)
            log.info("Telegram sent.")
            return True
        except Exception as e:
            wait = 2 ** attempt
            log.warning(f"Telegram attempt {attempt}/{retries} failed: {e}. Retry in {wait}s...")
            if attempt < retries:
                time.sleep(wait)
    log.error("All Telegram retries failed.")
    return False


def install_deps():
    for pkg in ["yfinance", "pandas", "numpy", "pytz"]:
        try:
            __import__(pkg)
        except ImportError:
            os.system(f"{sys.executable} -m pip install {pkg} -q --break-system-packages")

install_deps()
import yfinance as yf
import numpy as np
import pandas as pd


# ── State management ─────────────────────────────────────────────

def load_state():
    """
    Returns state dict:
      {
        "date": "2026-06-01",
        "signals": {"AAPL": "HOLD", "GOOGL": "BUY", ...},
        "alerted_events": ["AAPL:BOUNCE SETUP:2026-06-01 10:30", ...]
      }
    If state is from a previous day, reset signals (new day = fresh slate).
    """
    today = str(date.today())
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                state = json.load(f)
            if state.get("date") == today:
                return state
        except Exception:
            pass
    # Fresh state for today
    return {"date": today, "signals": {}, "alerted_events": []}

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log.error(f"Could not save state: {e}")

def event_key(sym, event_type):
    """Unique key for a (symbol, event_type) on today's date — prevents repeat alerts."""
    return f"{sym}:{event_type}:{date.today()}"


# ── Indicators ────────────────────────────────────────────────────

def safe_float(val):
    try:
        f = float(val)
        return f if np.isfinite(f) else None
    except Exception:
        return None

def calc_rsi(series, period=14):
    if len(series) < period + 1:
        return None
    delta = series.diff().dropna()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    lg = safe_float(loss.iloc[-1])
    gg = safe_float(gain.iloc[-1])
    if lg is None or gg is None:
        return None
    if lg == 0:
        return 100.0
    return safe_float(100 - 100 / (1 + gg / lg))

def get_daily_signal_label(sym):
    """
    Quick daily-bar signal (BUY / SELL / HOLD) using same logic as morning alert.
    Used to detect label changes between scans.
    """
    try:
        hist = yf.Ticker(sym).history(period="1y")
        if hist is None or len(hist) < 30:
            return None
        close = hist["Close"].dropna()
        price = safe_float(close.iloc[-1])
        if not price:
            return None

        rsi   = calc_rsi(close)
        ema50 = safe_float(close.ewm(span=50, adjust=False).mean().iloc[-1])
        ema200= safe_float(close.ewm(span=200, adjust=False).mean().iloc[-1])
        macd  = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
        sig   = macd.ewm(span=9, adjust=False).mean()
        macd_v  = safe_float(macd.iloc[-1])
        sig_v   = safe_float(sig.iloc[-1])
        support    = safe_float(hist["Low"].tail(60).min())
        resistance = safe_float(hist["High"].tail(60).max())

        if any(v is None for v in [rsi, ema50, ema200, support, resistance]):
            return None

        score = 0
        if rsi < 30:    score += 2
        elif rsi < 45:  score += 1
        elif rsi > 70:  score -= 2
        elif rsi > 60:  score -= 1

        if macd_v is not None and sig_v is not None:
            score += 1 if macd_v > sig_v else -1

        if price > ema50 and ema50 > ema200: score += 2
        elif price > ema50:                  score += 1
        elif price < ema50:                  score -= 1

        near_support    = (price - support) / price < 0.03
        near_resistance = (resistance - price) / price < 0.03
        if near_support:    score += 1
        if near_resistance: score -= 1

        if   score >= 4:  return "STRONG BUY"
        elif score >= 2:  return "BUY"
        elif score <= -4: return "STRONG SELL"
        elif score <= -2: return "SELL"
        else:             return "HOLD"
    except Exception as e:
        log.warning(f"{sym} daily signal error: {e}")
        return None


# ── Intraday event detection ──────────────────────────────────────

def scan_intraday_events(sym):
    """
    Detect intraday events: bounce setup, RSI oversold, momentum breakout,
    take-profit zone, resistance breakout.
    Returns list of event dicts.
    """
    events = []
    try:
        t     = yf.Ticker(sym)
        intra = t.history(period="1d", interval="5m")   # 5m bars (more stable than 1m)
        daily = t.history(period="1mo")

        if intra is None or intra.empty or daily is None or daily.empty:
            return events

        price    = safe_float(intra["Close"].iloc[-1])
        open_px  = safe_float(intra["Open"].iloc[0])
        if not price or not open_px or open_px == 0:
            return events

        intra_pct = (price - open_px) / open_px * 100

        # Volume: use avg daily volume from 1-month daily bars as baseline
        avg_vol  = safe_float(daily["Volume"].tail(20).mean())
        # Today's total intraday volume
        curr_vol = safe_float(intra["Volume"].sum())
        vol_spike = (curr_vol / avg_vol) if (avg_vol and avg_vol > 0 and curr_vol) else 1.0

        rsi        = calc_rsi(daily["Close"].dropna())
        support    = safe_float(daily["Low"].tail(60).min())
        resistance = safe_float(daily["High"].tail(60).max())

        if rsi is None or support is None or resistance is None:
            return events

        near_support    = (price - support) / price < 0.02
        near_resistance = (resistance - price) / price < 0.02

        # 1. Big intraday dip + near support = bounce setup
        if intra_pct <= INTRADAY_DIP and near_support:
            events.append({
                "type":   "BOUNCE SETUP",
                "sym":    sym,
                "price":  round(price, 2),
                "detail": f"Down {intra_pct:.1f}% from open, near support ${support:.2f}.",
                "action": "BUY THE DIP",
            })

        # 2. RSI oversold + intraday dip
        if rsi < RSI_OVERSOLD and intra_pct < 0:
            events.append({
                "type":   "RSI OVERSOLD",
                "sym":    sym,
                "price":  round(price, 2),
                "detail": f"RSI {rsi:.0f} deeply oversold. Down {intra_pct:.1f}% today.",
                "action": "STRONG BUY SIGNAL",
            })

        # 3. Momentum surge + volume confirmation
        if intra_pct >= INTRADAY_SURGE and vol_spike >= VOLUME_SPIKE:
            events.append({
                "type":   "MOMENTUM BREAKOUT",
                "sym":    sym,
                "price":  round(price, 2),
                "detail": f"Up {intra_pct:.1f}% on {vol_spike:.1f}x average volume.",
                "action": "RIDE THE MOMENTUM",
            })

        # 4. Near resistance + RSI overbought = take profit
        if near_resistance and rsi > RSI_OVERBOUGHT:
            events.append({
                "type":   "TAKE PROFIT",
                "sym":    sym,
                "price":  round(price, 2),
                "detail": f"Near resistance ${resistance:.2f}, RSI {rsi:.0f} overbought.",
                "action": "CONSIDER SELLING",
            })

        # 5. Breakout above resistance + volume
        if price > resistance * 0.998 and vol_spike >= 2.0 and intra_pct > 1.5:
            events.append({
                "type":   "RESISTANCE BREAKOUT",
                "sym":    sym,
                "price":  round(price, 2),
                "detail": f"Breaking above resistance ${resistance:.2f} on {vol_spike:.1f}x volume.",
                "action": "STRONG BUY — BREAKOUT",
            })

    except Exception as e:
        log.warning(f"{sym} intraday scan error: {e}")

    return events


# ── Message builders ──────────────────────────────────────────────

def build_change_message(label_changes, new_events, now_str):
    lines = [f"<b>McLean Trade Bot — Update {now_str}</b>"]

    if label_changes:
        lines.append("")
        lines.append("<b>── SIGNAL CHANGES ───────────────</b>")
        for sym, old_lbl, new_lbl, price in label_changes:
            day_indicator = "  <-- BUY" if "BUY" in new_lbl else ("  <-- SELL" if "SELL" in new_lbl else "")
            lines.append(f'<b>{sym}</b>  ${price}   {old_lbl} -> <b>{new_lbl}</b>{day_indicator}')

    if new_events:
        lines.append("")
        lines.append("<b>── INTRADAY ALERTS ──────────────</b>")
        for e in new_events:
            lines.append("")
            lines.append(f'<b>{e["sym"]}  ${e["price"]}</b> — {e["type"]}')
            lines.append(f'  {e["detail"]}')
            lines.append(f'  Action: <b>{e["action"]}</b>')

    lines.append("")
    lines.append("<i>McLean Trade Bot · Not financial advice</i>")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not is_market_hours():
        log.info(f"Outside market hours — skipping scan.")
        sys.exit(0)

    et      = pytz.timezone("America/New_York")
    now_et  = datetime.now(et)
    now_str = now_et.strftime("%I:%M %p ET")

    state = load_state()

    # Is this the first scan of the day? If so, just seed state, don't alert
    # (the 7AM daily alert already sent full details)
    is_first_scan = len(state["signals"]) == 0
    if is_first_scan:
        log.info("First scan of the day — seeding state, no alert sent.")
        for sym in PORTFOLIO:
            lbl = get_daily_signal_label(sym)
            if lbl:
                state["signals"][sym] = lbl
                log.info(f"  {sym}: {lbl}")
        save_state(state)
        sys.exit(0)

    # ── Subsequent scans: detect changes ─────────────────────────
    log.info(f"Scanning for changes at {now_str}...")

    label_changes = []   # (sym, old_label, new_label, price)
    new_events    = []   # intraday events not yet alerted today

    for sym in PORTFOLIO:
        # Check for daily label change
        new_lbl = get_daily_signal_label(sym)
        old_lbl = state["signals"].get(sym)

        if new_lbl and new_lbl != old_lbl:
            # Only report if moving to/from an actionable signal
            actionable = {"BUY", "STRONG BUY", "SELL", "STRONG SELL"}
            if new_lbl in actionable or (old_lbl and old_lbl in actionable):
                try:
                    price = round(yf.Ticker(sym).history(period="1d")["Close"].iloc[-1], 2)
                except Exception:
                    price = "?"
                label_changes.append((sym, old_lbl or "—", new_lbl, price))
                log.info(f"  {sym}: label changed {old_lbl} -> {new_lbl}")
            state["signals"][sym] = new_lbl

        # Check for new intraday events
        events = scan_intraday_events(sym)
        for ev in events:
            key = event_key(sym, ev["type"])
            if key not in state["alerted_events"]:
                new_events.append(ev)
                state["alerted_events"].append(key)
                log.info(f"  {sym}: new event [{ev['type']}]")

    save_state(state)

    if label_changes or new_events:
        msg = build_change_message(label_changes, new_events, now_str)
        log.info(f"Sending update: {len(label_changes)} changes, {len(new_events)} events")
        print(msg)
        send_telegram(msg)
    else:
        log.info("No changes — nothing sent.")
