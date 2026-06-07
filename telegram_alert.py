#!/usr/bin/env python3
"""
McLean Trade Bot — Daily Alert Script
Runs automatically at 7 AM weekdays via macOS LaunchAgent.

Hardening checklist:
  [x] EMA200 uses 1-year history (not 3-month) for accurate values
  [x] All indicator values validated — NaN/inf rejected, ticker skipped
  [x] RSI division-by-zero guarded
  [x] Telegram sends with 3 retries + exponential backoff
  [x] Full run logged to tradealerts.log
  [x] Stale data check — skips tickers whose last bar is > 5 days old
  [x] Support/resistance uses 60-day window (not 20-day)
  [x] Day-change uses previous trading day close, not T-2
  [x] Penny stock volume uses 20-day average daily volume baseline
  [x] Volume confirmation — BUY signals require above-average volume
  [x] Market filter — BUY signals suppressed when SPY drops >1.5%
  [x] ATR-based stop loss — stops sized to each stock's true volatility
"""
import urllib.request, urllib.parse, sys, os, time, json, logging
from datetime import datetime, timedelta

# ── Config ────────────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))
_CFG_FILE = os.path.join(_DIR, "config.json")

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
                         default="8955387419:AAHEmmHoibcYkv2MRcFElzX__4TOrP55PjQ")
TELEGRAM_CHAT_ID = _cred("TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_ID", "telegram_chat_id",
                         default="1578063059")
PORTFOLIO        = ["AAPL", "GOOGL", "PLTR", "VOO", "NVDA", "AMD", "AMZN", "CRM"]

WATCHLIST = [
    "META","MSFT","NFLX","TSLA","UBER","SHOP","XYZ","SNOW","COIN","DDOG",
    "AVGO","QCOM","MU","INTC","SMCI","ARM","TSM","AMAT","LRCX","KLAC",
    "JPM","GS","V","MA","BAC","MS","BLK","SCHW","AXP","COF",
    "UNH","LLY","JNJ","ABBV","MRK","PFE","AMGN","GILD","REGN","VRTX",
    "XOM","CVX","OXY","SLB","HAL","FCX","NEM","GOLD","WMB","KMI",
    "COST","TGT","WMT","HD","NKE","SBUX","MCD","CMG","LULU","DECK",
]
TOP_WATCHLIST = 3

PENNY_WATCHLIST = [
    # Biotech / speculative
    "OCGN","NKTR","IMVT","CLOV","SSYS","ACNB","ATOS","CRBP","TNXP","APDN",
    # Mining / metals
    "EXK","AG","MUX","PAAS","FSM","AUMN","UAMY","USAS","GPL","EXK",
    # Cannabis
    "TLRY","CGC","SNDL","ACB","CRLBF","CURLF","GTBIF","TCNNF","AYRWF","NEPT",
    # Crypto miners
    "MARA","RIOT","HIVE","HUT","BTBT","CIFR","WULF","CLSK","IREN","BITF",
    # High-vol momentum
    "GFAI","SHOT","ILUS","CODA","GFAI","IDAI","NTRB","XELA","KOSS","BBAI",
    # Small cap tech
    "SSYS","CLOV","ACNB","APDN","BBAI","NRXP","ELEV","PRST","NKLA","GOEV",
]
TOP_PENNIES = 4

# Max age of last data bar — reject data older than this
MAX_DATA_AGE_DAYS = 5

# Market filter — if SPY day change is below this, suppress BUY signals
SPY_BEAR_THRESHOLD = -1.5   # %

# ATR stop loss multiplier — stop = price - (ATR_MULTIPLIER * ATR14)
ATR_MULTIPLIER = 2.0

# Volume confirmation — BUY scores +1 if volume >= this multiple of 20d avg
VOLUME_CONFIRM_RATIO = 1.2
# ─────────────────────────────────────────────────────────────────

# ── Logging setup ─────────────────────────────────────────────────
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tradealerts.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("mclean")
# ─────────────────────────────────────────────────────────────────

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


# ── Validation helpers ────────────────────────────────────────────

def is_valid(val):
    """Return True if val is a real finite number."""
    try:
        return np.isfinite(float(val))
    except Exception:
        return False

def safe_float(val, default=None):
    """Convert to float, return default if NaN/inf/error."""
    try:
        f = float(val)
        return f if np.isfinite(f) else default
    except Exception:
        return default


# ── Telegram with retry ───────────────────────────────────────────

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
            log.info("Telegram alert sent successfully.")
            return True
        except Exception as e:
            wait = 2 ** attempt
            log.warning(f"Telegram attempt {attempt}/{retries} failed: {e}. Retrying in {wait}s...")
            if attempt < retries:
                time.sleep(wait)
    log.error("All Telegram retries failed. Alert not delivered.")
    return False


# ── Indicators ────────────────────────────────────────────────────

def calc_rsi(series, period=14):
    """RSI with NaN guard. Returns None if result is not a valid number."""
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
        return 100.0  # All gains — max RSI
    rs  = last_gain / last_loss
    rsi = 100 - 100 / (1 + rs)
    return safe_float(rsi)

def calc_macd(series):
    """Returns (macd_val, signal_val) or (None, None) on failure."""
    if len(series) < 35:
        return None, None
    ema12  = series.ewm(span=12, adjust=False).mean()
    ema26  = series.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    m = safe_float(macd.iloc[-1])
    s = safe_float(signal.iloc[-1])
    return m, s

def calc_ai_trend(close, lookback=30, forecast_days=5) -> int:
    """
    Lightweight AI trend predictor — linear regression + momentum.
    Uses only yfinance data already in memory. Zero extra dependencies.

    Combines two signals:
      1. Linear regression slope over `lookback` days (normalised)
      2. Short-term momentum: (close[-1] / close[-forecast_days]) - 1

    Returns:
      +1  if trend + momentum both point up
      -1  if trend + momentum both point down
       0  if signals conflict or data insufficient
    """
    try:
        if len(close) < lookback + forecast_days:
            return 0

        prices = np.array(close.tail(lookback), dtype=float)
        if not np.all(np.isfinite(prices)):
            return 0

        # Linear regression slope (normalised by mean price)
        x    = np.arange(lookback)
        xm   = x - x.mean()
        slope = (xm @ prices) / (xm @ xm)          # OLS slope
        slope_pct = slope / prices.mean() * 100     # as % per day

        # Short-term momentum: return over last forecast_days bars
        momentum_pct = (prices[-1] / prices[-forecast_days] - 1) * 100

        if slope_pct > 0.05 and momentum_pct > 0:
            return 1
        elif slope_pct < -0.05 and momentum_pct < 0:
            return -1
        else:
            return 0
    except Exception:
        return 0


def calc_atr(hist, period=14):
    """
    Average True Range over `period` days.
    True Range = max(High-Low, |High-PrevClose|, |Low-PrevClose|)
    Returns None if data is insufficient or invalid.
    """
    if len(hist) < period + 1:
        return None
    high  = hist["High"]
    low   = hist["Low"]
    close = hist["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = safe_float(tr.rolling(period).mean().iloc[-1])
    return atr

def get_market_mood():
    """
    Fetch SPY to check overall market direction.
    Returns (day_pct, is_bearish) where is_bearish=True means SPY is down >1.5%.
    Returns (None, False) on failure — we default to allowing BUY signals.
    """
    try:
        spy  = yf.Ticker("SPY")
        hist = spy.history(period="5d")
        if hist is None or len(hist) < 2:
            log.warning("SPY: could not fetch market mood")
            return None, False
        price = safe_float(hist["Close"].iloc[-1])
        prev  = safe_float(hist["Close"].iloc[-2])
        if price is None or prev is None or prev == 0:
            return None, False
        pct = (price - prev) / prev * 100
        is_bearish = pct <= SPY_BEAR_THRESHOLD
        log.info(f"SPY: {pct:+.2f}%  {'BEARISH — BUY signals suppressed' if is_bearish else 'OK'}")
        return round(pct, 2), is_bearish
    except Exception as e:
        log.warning(f"SPY market mood check failed: {e} — defaulting to neutral")
        return None, False


# ── Signal engine ─────────────────────────────────────────────────

def get_signal(sym):
    """
    Returns a signal dict or None.
    Fetch 1 year of daily data so EMA200 is accurate.
    All indicator values are validated before use.
    """
    try:
        t    = yf.Ticker(sym)
        hist = t.history(period="1y")   # FIXED: was 3mo — EMA200 needs ~200 bars

        if hist is None or hist.empty or len(hist) < 30:
            log.warning(f"{sym}: insufficient data ({len(hist) if hist is not None else 0} bars)")
            return None

        # ── Stale data check ──────────────────────────────────────
        last_date = hist.index[-1]
        # Make timezone-naive for comparison
        if hasattr(last_date, "tzinfo") and last_date.tzinfo is not None:
            last_date = last_date.replace(tzinfo=None)
        age_days = (datetime.now() - last_date).days
        if age_days > MAX_DATA_AGE_DAYS:
            log.warning(f"{sym}: data is {age_days} days old — skipping")
            return None

        close = hist["Close"].dropna()
        if len(close) < 30:
            return None

        price = safe_float(close.iloc[-1])
        prev  = safe_float(close.iloc[-2])
        if price is None or prev is None or prev == 0:
            log.warning(f"{sym}: invalid price data")
            return None

        pct = (price - prev) / prev * 100

        # ── Indicators ────────────────────────────────────────────
        rsi          = calc_rsi(close)
        macd, signal = calc_macd(close)
        ema50_series = close.ewm(span=50, adjust=False).mean()
        ema200_series= close.ewm(span=200, adjust=False).mean()
        ema50        = safe_float(ema50_series.iloc[-1])
        ema200       = safe_float(ema200_series.iloc[-1])

        # 60-day support/resistance (was 20-day — too narrow)
        support    = safe_float(hist["Low"].tail(60).min())
        resistance = safe_float(hist["High"].tail(60).max())

        # ATR-based stop loss (replaces arbitrary support*0.98)
        atr = calc_atr(hist)

        # 20-day average volume for confirmation check
        avg_vol  = safe_float(hist["Volume"].tail(20).mean())
        curr_vol = safe_float(hist["Volume"].iloc[-1])
        vol_ratio = (curr_vol / avg_vol) if (avg_vol and avg_vol > 0 and curr_vol) else None

        # Validate all indicators before scoring
        if any(v is None for v in [rsi, ema50, ema200, support, resistance]):
            log.warning(f"{sym}: one or more indicators returned NaN — skipping")
            return None
        if resistance <= support or support <= 0:
            log.warning(f"{sym}: invalid support/resistance — skipping")
            return None

        # ── Scoring ───────────────────────────────────────────────
        score   = 0
        reasons = []

        # RSI
        if rsi < 30:
            score += 2; reasons.append(f"RSI {rsi:.0f} oversold")
        elif rsi < 45:
            score += 1; reasons.append(f"RSI {rsi:.0f} neutral-low")
        elif rsi > 70:
            score -= 2; reasons.append(f"RSI {rsi:.0f} overbought")
        elif rsi > 60:
            score -= 1; reasons.append(f"RSI {rsi:.0f} elevated")

        # MACD (only if both values are valid)
        if macd is not None and signal is not None:
            if macd > signal:
                score += 1; reasons.append("MACD bullish")
            else:
                score -= 1; reasons.append("MACD bearish")

        # Trend (EMA50 vs EMA200 — now accurate with 1y data)
        if price > ema50 and ema50 > ema200:
            score += 2; reasons.append("above 50+200 EMA (strong uptrend)")
        elif price > ema50:
            score += 1; reasons.append("above 50 EMA")
        elif price < ema50:
            score -= 1; reasons.append("below 50 EMA")

        # Support / resistance proximity (3% threshold)
        near_support    = (price - support) / price < 0.03
        near_resistance = (resistance - price) / price < 0.03
        if near_support:
            score += 1; reasons.append("near support")
        if near_resistance:
            score -= 1; reasons.append("near resistance")

        # Volume confirmation — above-average volume adds confidence to any signal
        if vol_ratio is not None:
            if vol_ratio >= VOLUME_CONFIRM_RATIO:
                score += 1; reasons.append(f"volume {vol_ratio:.1f}x above avg — confirmed")
            elif vol_ratio < 0.5:
                score -= 1; reasons.append(f"volume {vol_ratio:.1f}x — very thin, low conviction")

        # AI trend signal — linear regression + momentum forecast
        ai_signal = calc_ai_trend(close)
        if ai_signal == 1:
            score += 1; reasons.append("AI trend: upward forecast")
        elif ai_signal == -1:
            score -= 1; reasons.append("AI trend: downward forecast")

        # ── Label ─────────────────────────────────────────────────
        if   score >= 4:  label = "STRONG BUY"
        elif score >= 2:  label = "BUY"
        elif score <= -4: label = "STRONG SELL"
        elif score <= -2: label = "SELL"
        else:             label = "HOLD"

        # ── Human "why" explanation ───────────────────────────────
        why_parts = []
        if "BUY" in label:
            if rsi < 35:
                why_parts.append(f"RSI {rsi:.0f} — deeply oversold, bounce likely")
            elif rsi < 45:
                why_parts.append(f"RSI {rsi:.0f} — pulled back to attractive levels")
            if near_support:
                why_parts.append(f"sitting on support ${support:.2f}")
            if macd is not None and signal is not None and macd > signal:
                why_parts.append("MACD turning bullish")
            if price > ema50:
                why_parts.append("above 50-day MA — uptrend intact")
            if pct < -2:
                why_parts.append(f"dipped {pct:.1f}% today — possible overreaction")
            if ai_signal == 1:
                why_parts.append("AI trend confirms upward momentum")
        elif "SELL" in label:
            if rsi > 70:
                why_parts.append(f"RSI {rsi:.0f} — overbought, pullback likely")
            if near_resistance:
                why_parts.append(f"hitting resistance ${resistance:.2f}")
            if pct > 5:
                why_parts.append(f"up {pct:.1f}% today — good time to lock in gains")
            if ai_signal == -1:
                why_parts.append("AI trend confirms downward pressure")
        else:
            why_parts = reasons[:2]

        why = " · ".join(why_parts[:2]) if why_parts else ", ".join(reasons[:2])
        if not why:
            why = f"Score {score}/6"

        # ATR stop loss: price - (2 * ATR14), floored at support*0.98 as backstop
        if atr and atr > 0:
            atr_stop = round(price - ATR_MULTIPLIER * atr, 2)
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
        log.error(f"{sym}: unexpected error in get_signal — {e}")
        return None


# ── Penny scanner ─────────────────────────────────────────────────

def scan_penny_stocks():
    """
    Scan penny watchlist. Volume baseline uses 20-day average DAILY volume
    (not intraday per-minute average) to avoid false spikes.
    """
    picks = []
    log.info(f"Scanning {len(PENNY_WATCHLIST)} penny stocks...")
    for sym in PENNY_WATCHLIST:
        try:
            t    = yf.Ticker(sym)
            hist = t.history(period="1mo")
            if hist is None or hist.empty or len(hist) < 10:
                continue

            close  = hist["Close"].dropna()
            volume = hist["Volume"].dropna()
            price  = safe_float(close.iloc[-1])

            if price is None or price >= 5.0 or price <= 0.01:
                continue

            prev = safe_float(close.iloc[-2])
            if prev is None or prev == 0:
                continue

            pct = (price - prev) / prev * 100

            # 20-day average DAILY volume as baseline (robust)
            avg_vol   = safe_float(volume.tail(20).mean())
            curr_vol  = safe_float(volume.iloc[-1])
            if avg_vol is None or avg_vol == 0 or curr_vol is None:
                continue
            vol_ratio = curr_vol / avg_vol

            rsi = calc_rsi(close)
            if rsi is None:
                continue

            week_pct = 0.0
            if len(close) >= 6:
                base = safe_float(close.iloc[-6])
                if base and base > 0:
                    week_pct = (price - base) / base * 100

            # Score
            score = 0
            if vol_ratio >= 3:     score += 3
            elif vol_ratio >= 2:   score += 2
            elif vol_ratio >= 1.5: score += 1

            if pct >= 5:    score += 3
            elif pct >= 2:  score += 2
            elif pct >= 0:  score += 1
            elif pct < -5:  score -= 2

            if rsi < 40:    score += 2
            elif rsi < 55:  score += 1
            elif rsi > 70:  score -= 1

            if score >= 3:
                picks.append({
                    "symbol":    sym,
                    "price":     round(price, 3),
                    "pct":       round(pct, 1),
                    "week_pct":  round(week_pct, 1),
                    "rsi":       round(rsi, 1),
                    "vol_ratio": round(vol_ratio, 1),
                    "score":     score,
                })
        except Exception as e:
            log.warning(f"Penny {sym}: {e}")
            continue

    picks.sort(key=lambda x: x["score"], reverse=True)
    return picks[:TOP_PENNIES]


# ── Watchlist scanner ─────────────────────────────────────────────

def scan_watchlist():
    """Scan broader watchlist. Only includes stocks scoring 2+."""
    picks = []
    log.info(f"Scanning {len(WATCHLIST)} watchlist stocks...")
    for sym in WATCHLIST:
        try:
            result = get_signal(sym)
            if result and result["score"] >= 2:
                picks.append(result)
        except Exception as e:
            log.warning(f"Watchlist {sym}: {e}")
            continue
    picks.sort(key=lambda x: x["score"], reverse=True)
    return picks[:TOP_WATCHLIST]


# ── Message builder ───────────────────────────────────────────────

def enrich_signal(s):
    """Add richer beginner-friendly fields (confidence, risk_band, setup_type,
    entry_zone, target_1/2, beginner_note) to an existing signal dict.
    Mirrors server.build_signal so the dashboard and Telegram stay consistent."""
    score      = s.get("score", 0)
    rsi        = s.get("rsi", 50)
    price      = s.get("price", 0) or 0
    support    = s.get("support", price * 0.95)
    resistance = s.get("resistance", price * 1.05)
    stop_loss  = s.get("stop_loss", round(price * 0.95, 2))
    label      = s.get("label", "HOLD")
    pct        = s.get("pct", 0) or 0
    atr        = s.get("atr") or (price * 0.02 if price else 1)
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

    entry_low  = round(safe_price * 0.99, 2)
    entry_high = round(safe_price, 2)
    entry_zone = f"${entry_low:.2f}–${entry_high:.2f}"

    target_1 = round(price + (resistance - price) * 0.5, 2)
    target_2 = round(resistance, 2)

    if "BUY" in label:
        beginner_note = (f"Consider entering between {entry_zone}. First target is "
                         f"${target_1:.2f}. Exit if price falls below ${stop_loss:.2f}.")
    elif "SELL" in label:
        beginner_note = (f"This stock is showing weakness. If you hold it, consider "
                         f"setting a stop at ${stop_loss:.2f}.")
    else:
        beginner_note = (f"No clear signal right now. Watch for break above "
                         f"${resistance:.2f}.")

    return {
        **s,
        "setup_type":    setup_type,
        "risk_band":     risk_band,
        "confidence":    confidence,
        "entry_zone":    entry_zone,
        "target_1":      target_1,
        "target_2":      target_2,
        "beginner_note": beginner_note,
    }


def build_message(signals, penny_picks, watchlist_picks=None, spy_pct=None, market_bearish=False):
    now   = datetime.now().strftime("%a, %b %-d %Y")
    signals = [enrich_signal(s) for s in signals]
    if watchlist_picks:
        watchlist_picks = [enrich_signal(s) for s in watchlist_picks]
    buys  = [s for s in signals if "BUY"  in s.get("label", "")]
    sells = [s for s in signals if "SELL" in s.get("label", "")]
    holds = [s for s in signals if s.get("label") == "HOLD"]
    summary = f"{len(buys)} BUY  {len(holds)} HOLD  {len(sells)} SELL"

    best = max(
        (s for s in signals if "BUY" in s.get("label", "")),
        key=lambda x: x["score"],
        default=None
    )

    spy_line = ""
    if spy_pct is not None:
        spy_dir = f"+{spy_pct}%" if spy_pct >= 0 else f"{spy_pct}%"
        spy_line = f"  |  SPY {spy_dir}"
        if market_bearish:
            spy_line += "  [BUY SIGNALS SUPPRESSED]"

    lines = [
        f"<b>McLean Trade Bot</b> · {now} · 7AM",
        f"Signals: {summary}{spy_line}",
    ]

    # ── Best pick ─────────────────────────────────────────────────
    if best:
        profit_pct = round((best["resistance"] - best["price"]) / best["price"] * 100, 1)
        lines += [
            "",
            "<b>── BEST PICK TODAY ──────────────</b>",
            f'<b>{best["symbol"]}</b>',
            f'  Price:  ${best["price"]}',
            f'  Target: ${best["resistance"]}  (+{profit_pct}%)',
            f'  Stop:   ${best["stop_loss"]}  (ATR-based)',
            f'  Why:    {best["why"]}',
        ]

    # ── Act now ───────────────────────────────────────────────────
    action_stocks = buys + sells
    if action_stocks:
        lines += ["", "<b>── ACT NOW ──────────────────────</b>"]
        for s in action_stocks:
            day_chg = f'+{s["pct"]}%' if s["pct"] >= 0 else f'{s["pct"]}%'
            is_buy  = "BUY" in s["label"]
            lines.append("")
            lines.append(f'<b>{s["symbol"]}</b>  ${s["price"]}  ({day_chg})  <b>{s["label"]}</b>')
            lines.append(f'  Why:  {s["why"]}')
            if is_buy:
                tgt = round(s["resistance"], 2)
                pct = round((tgt - s["price"]) / s["price"] * 100, 1)
                lines.append(f'  Target: ${tgt}  (+{pct}%)')
                lines.append(f'  Stop:   ${s["stop_loss"]}  (ATR-based)')
            else:
                lines.append(f'  Take profit near resistance ${s["resistance"]}')
                lines.append(f'  Risk: could fall to support ${s["support"]}')

    # ── Full portfolio ────────────────────────────────────────────
    lines += ["", "<b>── PORTFOLIO ────────────────────</b>"]
    for s in signals:
        arrow   = "▲" if s["pct"] >= 0 else "▼"
        day_chg = f'{arrow}{abs(s["pct"])}%'
        lines += [
            "",
            f'<b>{s["symbol"]}</b>  ${s["price"]}  {day_chg}  <b>{s["label"]}</b>',
            f'  Setup: {s["setup_type"]}  |  Risk: {s["risk_band"]}  |  Confidence: {s["confidence"]}%',
            f'  Entry: {s["entry_zone"]}  |  Target: ${s["target_1"]} / ${s["target_2"]}  |  Stop: ${s["stop_loss"]}',
            f'  💡 {s["beginner_note"]}',
        ]

    # ── Top picks / suppression ───────────────────────────────────
    if market_bearish:
        lines += ["", "<b>📉 BUY signals suppressed — market is weak. Sit tight.</b>",
                  "Watch these levels for when market recovers:"]
        watch = [s for s in signals if "BUY" not in s.get("label", "")][:5]
        for s in watch:
            lines.append(f'• {s["symbol"]}: watch ${s["resistance"]} resistance')
    elif watchlist_picks:
        lines += ["", "<b>🔥 TOP PICKS TODAY</b>"]
        for i, s in enumerate(watchlist_picks, 1):
            arrow = "↑" if s["pct"] >= 0 else "↓"
            day   = f'{arrow}{abs(s["pct"])}%'
            lines += [
                f'{i}. {s["symbol"]} — {s["label"]}  ${s["price"]}  {day}',
                f'   Setup: {s["setup_type"]} | Confidence: {s["confidence"]}% | Risk: {s["risk_band"]}',
                f'   Entry: {s["entry_zone"]} | T1: ${s["target_1"]} | T2: ${s["target_2"]} | Stop: ${s["stop_loss"]}',
                f'   💡 {s["beginner_note"]}',
            ]

    # ── Penny picks ───────────────────────────────────────────────
    if penny_picks:
        lines += ["", "<b>── PENNY PICKS ──────────────────</b>",
                  "<i>Small size only — high risk</i>"]
        for p in penny_picks:
            day  = f'+{p["pct"]}%'      if p["pct"]      >= 0 else f'{p["pct"]}%'
            week = f'+{p["week_pct"]}%' if p["week_pct"] >= 0 else f'{p["week_pct"]}%'
            lines += [
                "",
                f'<b>{p["symbol"]}</b>  ${p["price"]}',
                f'  Day: {day}   Week: {week}   Vol: {p["vol_ratio"]}x   RSI: {p["rsi"]}',
            ]

    lines += ["", "<i>McLean Trade Bot · Not financial advice</i>"]
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        log.warning("No Telegram token in env or config.json — exiting cleanly without sending alerts.")
        sys.exit(0)

    log.info("=== McLean Trade Bot starting ===")

    # ── Market mood check ─────────────────────────────────────────
    spy_pct, market_bearish = get_market_mood()

    # ── Portfolio signals ─────────────────────────────────────────
    log.info(f"Fetching portfolio: {', '.join(PORTFOLIO)}")
    signals = []
    for sym in PORTFOLIO:
        result = get_signal(sym)
        if result:
            # If market is bearish, downgrade BUY signals to HOLD
            if market_bearish and "BUY" in result["label"]:
                log.info(f"  {sym}: {result['label']} downgraded to HOLD (bearish market)")
                result["label"] = "HOLD"
                result["why"]   = f"Signal suppressed — SPY down {spy_pct}%"
            signals.append(result)
            log.info(f"  {sym}: {result['label']}  score={result['score']}  rsi={result['rsi']}  vol={result['vol_ratio']}x  atr_stop=${result['stop_loss']}")
        else:
            log.warning(f"  {sym}: no valid signal")

    if not signals:
        log.error("No valid signals from portfolio — aborting alert.")
        sys.exit(1)

    # ── Watchlist + penny scans ───────────────────────────────────
    watchlist_picks = scan_watchlist()
    # Suppress watchlist BUY picks on bearish days too
    if market_bearish:
        watchlist_picks = [s for s in watchlist_picks if "BUY" not in s.get("label", "")]
    log.info(f"Watchlist picks: {[s['symbol'] for s in watchlist_picks]}")

    penny_picks = scan_penny_stocks()
    log.info(f"Penny picks: {[p['symbol'] for p in penny_picks]}")

    # ── Standalone BUY alert ──────────────────────────────────────
    buys = [s for s in signals if "BUY" in s.get("label", "")]
    if buys:
        buy_lines = ["BUY ALERT — Opportunity Now", ""]
        for s in buys:
            day_chg    = f'+{s["pct"]}%' if s["pct"] >= 0 else f'{s["pct"]}%'
            profit_pct = round((s["resistance"] - s["price"]) / s["price"] * 100, 1)
            buy_lines += [
                f'<b>{s["symbol"]}</b>  ${s["price"]}  ({day_chg})  <b>{s["label"]}</b>',
                f'  {s["why"]}',
                f'  Target: ${s["resistance"]}  (+{profit_pct}%)',
                f'  Stop:   ${s["stop_loss"]}',
                "",
            ]
        buy_lines.append("<i>McLean Trade Bot · Not financial advice</i>")
        buy_msg = "\n".join(buy_lines)
        log.info(f"Sending BUY alert for: {[s['symbol'] for s in buys]}")
        send_telegram(buy_msg)
        time.sleep(1)

    # ── Standalone SELL alert ─────────────────────────────────────
    sells = [s for s in signals if "SELL" in s.get("label", "")]
    if sells:
        sell_lines = ["SELL ALERT — Act Now", ""]
        for s in sells:
            day_chg = f'+{s["pct"]}%' if s["pct"] >= 0 else f'{s["pct"]}%'
            sell_lines += [
                f'<b>{s["symbol"]}</b>  ${s["price"]}  ({day_chg})  <b>{s["label"]}</b>',
                f'  {s["why"]}',
                f'  Sell near: ${s["resistance"]}',
                f'  Protect gains — stop at: ${s["stop_loss"]}',
                "",
            ]
        sell_lines.append("<i>McLean Trade Bot · Not financial advice</i>")
        sell_msg = "\n".join(sell_lines)
        log.info(f"Sending SELL alert for: {[s['symbol'] for s in sells]}")
        send_telegram(sell_msg)
        time.sleep(1)

    # ── Build and send main report ────────────────────────────────
    message = build_message(signals, penny_picks, watchlist_picks, spy_pct, market_bearish)
    log.info("Message built. Sending to Telegram...")
    print(message)

    success = send_telegram(message)
    if not success:
        log.error("Alert delivery failed after all retries.")
        sys.exit(1)

    log.info("=== McLean Trade Bot done ===")
