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
_CFG_FILE = os.path.join(DIR, "config.json")

def _load_config():
    if os.path.exists(_CFG_FILE):
        try:
            return json.load(open(_CFG_FILE))
        except Exception:
            pass
    return {}

def _cred(env_key, *cfg_keys, default=""):
    """Prefer the env var, then config.json (accepts both key styles), then default."""
    val = os.environ.get(env_key)
    if val:
        return val
    cfg = _load_config()
    for k in cfg_keys:
        if cfg.get(k):
            return cfg[k]
    return default

TELEGRAM_TOKEN   = _cred("TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "telegram_token",
                         default="YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = _cred("TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_ID", "telegram_chat_id",
                         default="YOUR_CHAT_ID_HERE")
PORTFOLIO        = ["AAPL", "GOOGL", "PLTR", "VOO", "NVDA", "AMD", "AMZN", "CRM"]

# Alert only when signal type changes OR confidence shifts by more than this.
CONFIDENCE_SHIFT_THRESHOLD = 10

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

# Options strike picker — shared with the dashboard server. Safe to import:
# server.py only starts the HTTP server / background loop under __main__.
try:
    from server import get_options_suggestion
except Exception as e:
    log.warning(f"Options suggestions unavailable: {e}")
    get_options_suggestion = None

def _options_line(sym, label, detail):
    """One-line options idea for a freshly-changed BUY/SELL signal."""
    if not get_options_suggestion or not detail:
        return None
    o = get_options_suggestion(sym, detail)
    if not o or not o.get("near"):
        return None
    kind = "CALL" if o["basis"] == "support" else "PUT"
    leg  = o["near"]
    prem = leg.get("mid_price") if leg.get("mid_price") is not None else leg.get("last_price")
    prem_s = f"${prem:.2f}" if prem is not None else "—"
    return f'  Options idea: {kind} ${leg["strike"]} exp {leg["expiry"]} (premium ~{prem_s}) — strike near {o["basis"]} ${o["basis_price"]}'
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

def build_signal(s):
    """Take a base signal dict and return it enriched with the richer
    beginner-friendly fields, preserving all original fields."""
    score = s.get("score", 0)
    rsi = s.get("rsi", 50)
    price = s.get("price", 0) or 0
    support = s.get("support", price * 0.95)
    resistance = s.get("resistance", price * 1.05)
    stop_loss = s.get("stop_loss", round(price * 0.95, 2))
    label = s.get("label", "HOLD")
    pct = s.get("pct", 0) or 0
    atr = s.get("atr") or (price * 0.02 if price else 1)
    safe_price = price if price else 1

    if "BUY" in label:
        rsi_contrib = max(0, min(40, int((rsi - 30) / 40 * 40)))
    else:
        rsi_contrib = max(0, min(40, int((70 - rsi) / 40 * 40)))
    confidence = min(95, max(10, abs(score) * 12 + rsi_contrib))

    if atr / safe_price > 0.04 or abs(pct) > 3:
        risk_band = "High"
    elif atr / safe_price > 0.02 or abs(pct) > 1.5:
        risk_band = "Medium"
    else:
        risk_band = "Low"

    if score >= 4:
        setup_type = "Breakout" if price > resistance * 0.98 else "Momentum"
    elif score <= -2:
        setup_type = "Breakdown"
    elif score <= 1 and label == "HOLD":
        setup_type = "Range"
    elif rsi < 35:
        setup_type = "Reversal"
    else:
        setup_type = "Trend"

    entry_low = round(safe_price * 0.99, 2)
    entry_high = round(safe_price, 2)
    entry_zone = f"${entry_low:.2f} – ${entry_high:.2f}"
    target_1 = round(price + (resistance - price) * 0.5, 2)
    target_2 = round(resistance, 2)

    if "BUY" in label:
        beginner_note = (f"Consider entering between {entry_zone}. First target is "
                         f"${target_1:.2f}. Exit if price falls below ${stop_loss:.2f}.")
    elif "SELL" in label:
        beginner_note = (f"This stock is showing weakness. If you hold it, consider "
                         f"setting a stop at ${stop_loss:.2f}.")
    else:
        beginner_note = (f"No clear signal right now. Watch for price to break above "
                         f"${resistance:.2f} or below ${support:.2f} before acting.")

    return {
        **s,
        "ticker": s.get("symbol", ""),
        "signal": label,
        "setup_type": setup_type,
        "risk_band": risk_band,
        "confidence": confidence,
        "entry_zone": entry_zone,
        "target_1": target_1,
        "target_2": target_2,
        "invalidation": stop_loss,
        "beginner_note": beginner_note,
    }


def get_daily_signal(sym):
    """
    Quick daily-bar signal using same logic as morning alert.
    Returns a full signal dict enriched via build_signal(), or None.
    """
    return get_daily_signal_detail(sym)

def get_daily_signal_detail(sym):
    """
    Same as get_daily_signal_label but returns price/support/resistance too,
    so callers can build an options strike suggestion when the label changes.
    """
    try:
        hist = yf.Ticker(sym).history(period="1y")
        if hist is None or len(hist) < 30:
            return None
        close = hist["Close"].dropna()
        price = safe_float(close.iloc[-1])
        prev  = safe_float(close.iloc[-2]) if len(close) >= 2 else None
        if not price:
            return None
        pct = ((price - prev) / prev * 100) if (prev and prev != 0) else 0.0

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

        if   score >= 4:  label = "STRONG BUY"
        elif score >= 2:  label = "BUY"
        elif score <= -4: label = "STRONG SELL"
        elif score <= -2: label = "SELL"
        else:             label = "HOLD"

        stop_loss = round(support * 0.98, 2)
        base = {
            "symbol": sym, "price": round(price, 2), "pct": round(pct, 2),
            "rsi": round(rsi, 1), "score": score, "label": label,
            "support": round(support, 2), "resistance": round(resistance, 2),
            "stop_loss": stop_loss,
        }
        return build_signal(base)
    except Exception as e:
        log.warning(f"{sym} daily signal error: {e}")
        return None


def get_daily_signal_label(sym):
    """Backward-compatible wrapper — returns just the label string or None."""
    sig = get_daily_signal(sym)
    return sig["label"] if sig else None


def format_intraday_alert(signal):
    """Format the richer signal fields into a readable Telegram message."""
    s = signal
    day_chg = f'+{s["pct"]}%' if s.get("pct", 0) >= 0 else f'{s["pct"]}%'
    lines = [
        f'<b>{s["symbol"]}</b>  ${s["price"]}  ({day_chg})  <b>{s["label"]}</b>',
        f'  Setup: {s.get("setup_type","—")}   Risk: {s.get("risk_band","—")}   Confidence: {s.get("confidence","—")}%',
    ]
    if s.get("entry_zone"):
        lines.append(f'  Entry zone: {s["entry_zone"]}')
    if s.get("target_1") is not None:
        lines.append(f'  Target 1: ${s["target_1"]}   Target 2: ${s.get("target_2","—")}')
    if s.get("invalidation") is not None:
        lines.append(f'  Invalidation (stop): ${s["invalidation"]}')
    if s.get("beginner_note"):
        lines.append(f'  <i>{s["beginner_note"]}</i>')
    return "\n".join(lines)


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

def build_change_message(label_changes, new_events, now_str, rich_signals=None):
    rich_signals = rich_signals or {}
    lines = [f"<b>McLean Trade Bot — Update {now_str}</b>"]

    if label_changes:
        lines.append("")
        lines.append("<b>── SIGNAL CHANGES ───────────────</b>")
        for sym, old_lbl, new_lbl, price, detail in label_changes:
            day_indicator = "  <-- BUY" if "BUY" in new_lbl else ("  <-- SELL NOW" if "SELL" in new_lbl else "")
            lines.append(f'<b>{sym}</b>  ${price}   {old_lbl} -> <b>{new_lbl}</b>{day_indicator}')
            if sym in rich_signals:
                lines.append(format_intraday_alert(rich_signals[sym]))
            opt_line = _options_line(sym, new_lbl, detail)
            if opt_line: lines.append(opt_line)

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

def build_standalone_alerts(label_changes, new_events, now_str):
    """
    Returns (buy_msg, sell_msg) — standalone notifications sent before the main update.
    Either can be None if no relevant signals.
    """
    buy_changes  = [c for c in label_changes if "BUY" in c[2]]
    sell_changes = [c for c in label_changes if "SELL" in c[2]]

    buy_events  = [e for e in new_events if e["type"] in ("BOUNCE SETUP", "RSI OVERSOLD", "MOMENTUM BREAKOUT", "RESISTANCE BREAKOUT")]
    sell_events = [e for e in new_events if e["type"] == "TAKE PROFIT"]

    # ── BUY alert ────────────────────────────────────────────────
    buy_msg = None
    if buy_changes or buy_events:
        lines = ["BUY ALERT — Opportunity Now", ""]
        for sym, old_lbl, new_lbl, price, detail in buy_changes:
            lines += [
                f'<b>{sym}  ${price}</b>  {old_lbl} -> <b>{new_lbl}</b>',
                f'  Signal flipped to BUY — entry opportunity now.',
            ]
            opt_line = _options_line(sym, new_lbl, detail)
            if opt_line: lines.append(opt_line)
            lines.append("")
        for e in buy_events:
            lines += [
                f'<b>{e["sym"]}  ${e["price"]}</b> — {e["type"]}',
                f'  {e["detail"]}',
                f'  Action: <b>{e["action"]}</b>',
                "",
            ]
        lines.append("<i>McLean Trade Bot · Not financial advice</i>")
        buy_msg = "\n".join(lines)

    # ── SELL alert ────────────────────────────────────────────────
    sell_msg = None
    if sell_changes or sell_events:
        lines = ["SELL ALERT — Act Now", ""]
        for sym, old_lbl, new_lbl, price, detail in sell_changes:
            lines += [
                f'<b>{sym}  ${price}</b>  {old_lbl} -> <b>{new_lbl}</b>',
                f'  Signal flipped to SELL — consider taking profit now.',
            ]
            opt_line = _options_line(sym, new_lbl, detail)
            if opt_line: lines.append(opt_line)
            lines.append("")
        for e in sell_events:
            lines += [
                f'<b>{e["sym"]}  ${e["price"]}</b> — {e["type"]}',
                f'  {e["detail"]}',
                f'  Action: <b>{e["action"]}</b>',
                "",
            ]
        lines.append("<i>McLean Trade Bot · Not financial advice</i>")
        sell_msg = "\n".join(lines)

    return buy_msg, sell_msg


# ── Main ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == "YOUR_BOT_TOKEN_HERE":
        log.warning("No Telegram token in env or config.json — exiting cleanly without sending alerts.")
        sys.exit(0)

    if not is_market_hours():
        log.info(f"Outside market hours — skipping scan.")
        sys.exit(0)

    et      = pytz.timezone("America/New_York")
    now_et  = datetime.now(et)
    now_str = now_et.strftime("%I:%M %p ET")

    state = load_state()

    def old_entry(sym):
        """Read prior state for a ticker, tolerating old (string) state format."""
        v = state["signals"].get(sym)
        if isinstance(v, dict):
            return v.get("label"), v.get("confidence")
        return v, None  # legacy: label string only

    # Is this the first scan of the day? If so, just seed state, don't alert
    # (the 7AM daily alert already sent full details)
    is_first_scan = len(state["signals"]) == 0
    if is_first_scan:
        log.info("First scan of the day — seeding state, no alert sent.")
        for sym in PORTFOLIO:
            sig = get_daily_signal(sym)
            if sig:
                state["signals"][sym] = {"label": sig["label"], "confidence": sig["confidence"]}
                log.info(f"  {sym}: {sig['label']} ({sig['confidence']}%)")
        save_state(state)
        sys.exit(0)

    # ── Subsequent scans: detect changes ─────────────────────────
    log.info(f"Scanning for changes at {now_str}...")

    label_changes = []   # (sym, old_label, new_label, price)
    new_events    = []   # intraday events not yet alerted today
    rich_signals  = {}   # sym -> enriched signal dict (for richer alert formatting)

    for sym in PORTFOLIO:
        # Check for daily label / confidence change
        sig = get_daily_signal(sym)
        new_lbl  = sig["label"] if sig else None
        new_conf = sig["confidence"] if sig else None
        old_lbl, old_conf = old_entry(sym)

        if sig:
            rich_signals[sym] = sig
            label_changed = new_lbl != old_lbl
            conf_shifted  = old_conf is not None and abs(new_conf - old_conf) >= CONFIDENCE_SHIFT_THRESHOLD
            if label_changed or conf_shifted:
                # Only report if moving to/from an actionable signal
                actionable = {"BUY", "STRONG BUY", "SELL", "STRONG SELL"}
                if new_lbl in actionable or (old_lbl and old_lbl in actionable):
                    price = round(sig["price"], 2) if sig.get("price") else "?"
                    label_changes.append((sym, old_lbl or "—", new_lbl, price, sig))
                    reason = "label change" if label_changed else f"confidence {old_conf}%→{new_conf}%"
                    log.info(f"  {sym}: {reason}  {old_lbl} -> {new_lbl}")
            state["signals"][sym] = {"label": new_lbl, "confidence": new_conf}

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
        # Send standalone BUY / SELL alerts first — arrive as separate notifications
        buy_msg, sell_msg = build_standalone_alerts(label_changes, new_events, now_str)
        if buy_msg:
            log.info("Sending standalone BUY alert...")
            send_telegram(buy_msg)
            time.sleep(1)
        if sell_msg:
            log.info("Sending standalone SELL alert...")
            send_telegram(sell_msg)
            time.sleep(1)

        # Then send the full change summary
        msg = build_change_message(label_changes, new_events, now_str, rich_signals)
        log.info(f"Sending update: {len(label_changes)} changes, {len(new_events)} events")
        print(msg)
        send_telegram(msg)
    else:
        log.info("No changes — nothing sent.")
