#!/usr/bin/env python3
"""
McLean Trade Bot — Dashboard Server
Run:  python3 server.py
Open: http://localhost:8080/dashboard.html
"""
import json, os, sys, time, threading, logging, urllib.request, urllib.parse, warnings
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Install deps ──────────────────────────────────────────────────
for pkg in ["yfinance", "pandas", "numpy"]:
    try:
        __import__(pkg)
    except ImportError:
        print(f"Installing {pkg}...")
        os.system(f"{sys.executable} -m pip install {pkg} -q --break-system-packages")

import yfinance as yf
import pandas as pd
import numpy as np

DIR         = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(DIR, "config.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(DIR, "server.log")),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger("server")

# ── Config ────────────────────────────────────────────────────────
def read_config():
    try:
        if os.path.exists(CONFIG_FILE):
            return json.load(open(CONFIG_FILE))
    except Exception:
        pass
    return {}

def write_config(updates):
    cfg = read_config()
    cfg.update(updates)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

FALLBACK_TOKEN   = "8955387419:AAHEmmHoibcYkv2MRcFElzX__4TOrP55PjQ"
FALLBACK_CHAT_ID = "1578063059"

def get_creds():
    cfg = read_config()
    token = (os.environ.get("TELEGRAM_BOT_TOKEN")
             or cfg.get("TELEGRAM_BOT_TOKEN") or cfg.get("telegram_token") or FALLBACK_TOKEN)
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID")
               or cfg.get("TELEGRAM_CHAT_ID") or cfg.get("telegram_chat_id") or FALLBACK_CHAT_ID)
    return token, chat_id

def send_telegram(msg):
    token, chat_id = get_creds()
    try:
        url  = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": msg, "parse_mode": "HTML"}).encode()
        urllib.request.urlopen(urllib.request.Request(url, data=data, method="POST"), timeout=10)
        return True
    except Exception as e:
        log.warning(f"Telegram: {e}")
        return False

# ── Watchlists ────────────────────────────────────────────────────
US_PORTFOLIO = ["AAPL","GOOGL","PLTR","VOO","NVDA","AMD","AMZN","CRM"]
US_WATCHLIST = [
    "META","MSFT","NFLX","TSLA","UBER","AVGO","QCOM","MU","JPM","GS",
    "V","MA","UNH","LLY","XOM","CVX","COST","WMT","HD","NKE",
]
LARGECAP_WATCHLIST = [
    "MSFT","META","AMZN","NVDA","GOOGL","AAPL","TSLA","BRK-B","JPM","V",
    "UNH","XOM","LLY","JNJ","WMT","MA","PG","HD","MRK","ABBV",
    "AVGO","CVX","PEP","KO","COST","ADBE","CRM","TMO","MCD","ACN",
    "BAC","NFLX","AMD","QCOM","TXN","INTC","NOW","INTU","IBM","GS",
    "MS","RTX","CAT","DE","BA","UPS","HON","AMGN","GILD","PFE"
]
PENNY_LIST = [
    # Crypto miners
    "HIVE","HUT","BTBT","WULF","CIFR","BITF",
    # Cannabis
    "SNDL","ACB","TLRY","CGC",
    # Biotech speculative
    "OCGN","BBAI","NKTR","CLOV","ATOS",
    # Small cap / momentum
    "IDAI","GFAI","KOSS","XELA","SHOT",
    # Miners / metals
    "EXK","MUX","UAMY","USAS","FSM",
]
PENNY_MAX_USD = 5.0   # US penny = under $5

INDIA_PORTFOLIO = ["RELIANCE","TCS","INFY","HDFCBANK","ICICIBANK","WIPRO","BAJFINANCE","TITAN","ASIANPAINT","MARUTI"]
INDIA_WATCHLIST = [
    "HINDUNILVR","NESTLEIND","KOTAKBANK","SUNPHARMA","DRREDDY",
    "ADANIPORTS","POWERGRID","NTPC","TATAMOTORS","ITC",
    "JSWSTEEL","TATASTEEL","AXISBANK","PERSISTENT","NYKAA",
]
INDIA_PENNY_LIST = [
    # Banking / finance under ₹30
    "YESBANK","SOUTHBANK","UJJIVANSFB","BANDHANBNK",
    # Telecom
    "IDEA",
    # Power / infra
    "JPPOWER","JPASSOCIAT","RPOWER","GMRAIRPORT",
    # Media
    "NETWORK18","SUNTV","HATHWAY",
    # Small cap misc
    "SUZLON","IRB","DBCORP","ORIENTCEM","RAIN",
]
PENNY_MAX_INR = 30.0  # India penny = under ₹30

# ── Indicators ────────────────────────────────────────────────────
def sf(v, d=None):
    try:
        f = float(v)
        return f if np.isfinite(f) else d
    except:
        return d

def calc_rsi(s, p=14):
    if len(s) < p+1: return None
    d = s.diff().dropna()
    g = d.clip(lower=0).rolling(p).mean()
    l = (-d.clip(upper=0)).rolling(p).mean()
    lg, ll = sf(g.iloc[-1]), sf(l.iloc[-1])
    if lg is None or ll is None: return None
    if ll == 0: return 100.0
    return sf(100 - 100/(1+lg/ll))

def calc_macd(s):
    if len(s) < 35: return None, None
    macd = s.ewm(span=12,adjust=False).mean() - s.ewm(span=26,adjust=False).mean()
    sig  = macd.ewm(span=9,adjust=False).mean()
    return sf(macd.iloc[-1]), sf(sig.iloc[-1])

def calc_ai(close, lb=30, fd=5):
    try:
        if len(close) < lb+fd: return 0
        p  = np.array(close.tail(lb), dtype=float)
        if not np.all(np.isfinite(p)): return 0
        x  = np.arange(lb); xm = x - x.mean()
        sl = (xm @ p)/(xm @ xm)
        sp = sl/p.mean()*100
        mp = (p[-1]/p[-fd]-1)*100
        if sp > 0.05 and mp > 0: return 1
        if sp < -0.05 and mp < 0: return -1
        return 0
    except: return 0

def calc_atr(hist, p=14):
    if len(hist) < p+1: return None
    h,l,c = hist["High"], hist["Low"], hist["Close"]
    pc = c.shift(1)
    tr = pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1)
    return sf(tr.rolling(p).mean().iloc[-1])

# ── Batch download — parallel individual fetches with per-ticker timeout ──
def _fetch_one(sym, ticker_sym, period):
    """Fetch one ticker. Returns (sym, DataFrame) or (sym, None)."""
    try:
        df = yf.Ticker(ticker_sym).history(period=period, auto_adjust=True)
        if df is not None and not df.empty:
            return sym, df
    except Exception as e:
        log.debug(f"_fetch_one {sym}: {e}")
    return sym, None

def batch_dl(symbols, period="1y", ns=False, timeout=20):
    """Fetch OHLCV for all symbols in parallel. Each ticker gets `timeout` seconds."""
    if not symbols: return {}
    fetch_map = {s: (s+".NS" if ns and not s.endswith(".NS") else s) for s in symbols}
    out = {}
    with ThreadPoolExecutor(max_workers=min(8, len(symbols))) as ex:
        futures = {ex.submit(_fetch_one, sym, ts, period): sym for sym, ts in fetch_map.items()}
        for fut in as_completed(futures, timeout=timeout * len(symbols)):
            try:
                sym, df = fut.result(timeout=timeout)
                if df is not None:
                    out[sym] = df
            except Exception as e:
                log.debug(f"batch_dl future error: {e}")
    return out

# ── Score a stock from its history DataFrame ──────────────────────
def score(sym, hist, ns=False):
    try:
        if hist is None or hist.empty or len(hist) < 30: return None
        last = hist.index[-1]
        if hasattr(last,"tzinfo") and last.tzinfo:
            last = last.replace(tzinfo=None)
        if (datetime.now()-last).days > 5: return None

        close = hist["Close"].dropna()
        if len(close) < 30: return None
        price = sf(close.iloc[-1]); prev = sf(close.iloc[-2])
        if not price or not prev or prev == 0: return None
        pct = (price-prev)/prev*100

        rsi     = calc_rsi(close)
        macd,sg = calc_macd(close)
        e50     = sf(close.ewm(span=50, adjust=False).mean().iloc[-1])
        e200    = sf(close.ewm(span=200,adjust=False).mean().iloc[-1])
        supp    = sf(hist["Low"].tail(60).min())
        res     = sf(hist["High"].tail(60).max())
        atr     = calc_atr(hist)
        avgvol  = sf(hist["Volume"].tail(20).mean())
        curvol  = sf(hist["Volume"].iloc[-1])
        vr      = (curvol/avgvol) if (avgvol and avgvol>0 and curvol) else None

        if any(v is None for v in [rsi,e50,e200,supp,res]): return None
        if res<=supp or supp<=0: return None

        sc,rs = 0,[]
        if rsi<30:   sc+=2; rs.append(f"RSI {rsi:.0f} oversold")
        elif rsi<45: sc+=1; rs.append(f"RSI {rsi:.0f} neutral-low")
        elif rsi>70: sc-=2; rs.append(f"RSI {rsi:.0f} overbought")
        elif rsi>60: sc-=1; rs.append(f"RSI {rsi:.0f} elevated")
        if macd is not None and sg is not None:
            if macd>sg: sc+=1; rs.append("MACD bullish")
            else:       sc-=1; rs.append("MACD bearish")
        if price>e50 and e50>e200: sc+=2; rs.append("above 50+200 EMA (strong uptrend)")
        elif price>e50: sc+=1; rs.append("above 50 EMA")
        elif price<e50: sc-=1; rs.append("below 50 EMA")
        nsupp = (price-supp)/price < 0.03
        nres  = (res-price)/price  < 0.03
        if nsupp: sc+=1; rs.append("near support")
        if nres:  sc-=1; rs.append("near resistance")
        if vr:
            if vr>=1.2: sc+=1; rs.append(f"volume {vr:.1f}x above avg")
            elif vr<0.5: sc-=1; rs.append(f"volume {vr:.1f}x — thin")
        ai = calc_ai(close)
        if ai==1:  sc+=1; rs.append("AI trend: upward forecast")
        elif ai==-1: sc-=1; rs.append("AI trend: downward forecast")

        if   sc>=4:  lb="STRONG BUY"
        elif sc>=2:  lb="BUY"
        elif sc<=-4: lb="STRONG SELL"
        elif sc<=-2: lb="SELL"
        else:        lb="HOLD"

        cur = "₹" if ns else "$"
        wp=[]
        if "BUY" in lb:
            if rsi<35: wp.append(f"RSI {rsi:.0f} — deeply oversold")
            elif rsi<45: wp.append(f"RSI {rsi:.0f} — pulled back")
            if nsupp: wp.append(f"sitting on support {cur}{supp:.2f}")
            if macd and sg and macd>sg: wp.append("MACD turning bullish")
            if price>e50: wp.append("above 50-day MA — uptrend intact")
            if pct<-2: wp.append(f"dipped {pct:.1f}% today")
            if ai==1: wp.append("AI trend confirms upward momentum")
        elif "SELL" in lb:
            if rsi>70: wp.append(f"RSI {rsi:.0f} — overbought")
            if nres: wp.append(f"hitting resistance {cur}{res:.2f}")
            if pct>5: wp.append(f"up {pct:.1f}% today — lock in gains")
            if ai==-1: wp.append("AI trend confirms downward pressure")
        else:
            wp = rs[:2]
        why = " · ".join(wp[:2]) if wp else ", ".join(rs[:2]) or f"Score {sc}"

        stop = max(round(price-2*atr,2), round(supp*0.98,2)) if (atr and atr>0) else round(supp*0.98,2)
        return {
            "symbol":sym,"price":round(price,2),"pct":round(pct,2),
            "rsi":round(rsi,1),"score":sc,"label":lb,"why":why,
            "support":round(supp,2),"resistance":round(res,2),"stop_loss":stop,
            "profit_pct":round((res-price)/price*100,1),
            "atr":round(atr,2) if atr else None,
            "vol_ratio":round(vr,1) if vr else None,
            "ai_signal":ai,"reasons":rs,
        }
    except Exception as e:
        log.debug(f"{sym} score error: {e}")
        return None

def score_penny(sym, hist, max_price=5.0):
    try:
        if hist is None or hist.empty or len(hist)<10: return None
        close  = hist["Close"].dropna()
        volume = hist["Volume"].dropna()
        price  = sf(close.iloc[-1])
        if price is None or price>=max_price or price<=0.01: return None
        prev = sf(close.iloc[-2])
        if not prev or prev==0: return None
        pct = (price-prev)/prev*100
        avgv = sf(volume.tail(20).mean()); curv = sf(volume.iloc[-1])
        if not avgv or avgv==0 or not curv: return None
        vr   = curv/avgv
        rsi  = calc_rsi(close)
        if rsi is None: return None
        wk = ((price-sf(close.iloc[-6]))/sf(close.iloc[-6])*100) if len(close)>=6 and sf(close.iloc[-6]) else 0.0
        sc = 0
        if vr>=3: sc+=3
        elif vr>=2: sc+=2
        elif vr>=1.5: sc+=1
        if pct>=5: sc+=3
        elif pct>=2: sc+=2
        elif pct>=0: sc+=1
        elif pct<-5: sc-=2
        if rsi<40: sc+=2
        elif rsi<55: sc+=1
        elif rsi>70: sc-=1
        if sc<2: return None
        return {"symbol":sym,"price":round(price,3),"pct":round(pct,1),
                "week_pct":round(wk,1),"rsi":round(rsi,1),"vol_ratio":round(vr,1),"score":sc}
    except:
        return None

# ── Richer signal schema ──────────────────────────────────────────
def build_signal(s):
    """Take an existing signal dict and return it with all original fields
    PLUS the richer beginner-friendly fields. Never removes existing fields."""
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

    # confidence: score contributes ~60%, rsi momentum contributes ~40%
    if label == "BUY" or label == "STRONG BUY":
        rsi_contrib = max(0, min(40, int((rsi - 30) / 40 * 40)))
    else:
        rsi_contrib = max(0, min(40, int((70 - rsi) / 40 * 40)))
    confidence = min(95, max(10, abs(score) * 12 + rsi_contrib))

    # risk band
    if atr / safe_price > 0.04 or abs(pct) > 3:
        risk_band = "High"
    elif atr / safe_price > 0.02 or abs(pct) > 1.5:
        risk_band = "Medium"
    else:
        risk_band = "Low"

    # setup type
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

    # entry zone
    entry_low = round(safe_price * 0.99, 2)
    entry_high = round(safe_price, 2)
    entry_zone = f"${entry_low:.2f} – ${entry_high:.2f}"

    # targets
    target_1 = round(price + (resistance - price) * 0.5, 2)
    target_2 = round(resistance, 2)

    # beginner note
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

# ── Market mood ───────────────────────────────────────────────────
def market_mood(proxy):
    try:
        h = yf.Ticker(proxy).history(period="5d")
        if h is None or len(h)<2: return None, False
        p1 = sf(h["Close"].iloc[-1]); p2 = sf(h["Close"].iloc[-2])
        if not p1 or not p2 or p2==0: return None, False
        pct = (p1-p2)/p2*100
        return round(pct,2), pct<=-1.5
    except:
        return None, False

# ── Scan cache ────────────────────────────────────────────────────
_lock = threading.Lock()
_cache = {
    "us":    {"signals":[],"penny_picks":[],"watchlist_picks":[],"spy_pct":None,"market_bearish":False,"last_updated":None,"scanning":False,"error":None},
    "india": {"signals":[],"watchlist_picks":[],"penny_picks":[],"nifty_pct":None,"market_bearish":False,"last_updated":None,"scanning":False,"error":None},
}
_top_picks_cache = {
    "us": {"suppressed":False,"reason":None,"picks":[],"last_updated":None},
}

def run_us_scan():
    log.info("=== US scan start ===")
    with _lock: _cache["us"]["scanning"]=True; _cache["us"]["error"]=None
    try:
        spy_pct, bearish = market_mood("SPY")
        cfg = read_config()
        port = cfg.get("us_portfolio", US_PORTFOLIO)

        # Phase 1 — portfolio (batch, ~5 sec)
        log.info(f"Downloading {len(port)} portfolio stocks...")
        ph = batch_dl(port, "1y")
        signals = []
        for sym in port:
            r = score(sym, ph.get(sym))
            if r:
                if bearish and "BUY" in r["label"]:
                    r["label"]="HOLD"; r["why"]=f"Suppressed — SPY down {spy_pct}%"
                r = build_signal(r)
                signals.append(r)
                log.info(f"  {sym}: {r['label']}  score={r['score']}")
            else:
                log.warning(f"  {sym}: no signal")

        with _lock:
            _cache["us"].update({"signals":signals,"spy_pct":spy_pct,"market_bearish":bearish,
                                  "last_updated":datetime.now().isoformat(),"scanning":True})
        log.info(f"Portfolio done ({len(signals)}). Downloading watchlist...")

        # Phase 2 — watchlist (batch)
        wl = cfg.get("us_watchlist", US_WATCHLIST)
        wh = batch_dl(wl, "1y")
        wl_picks = []
        for sym in wl:
            r = score(sym, wh.get(sym))
            if r and r["score"]>=2: wl_picks.append(build_signal(r))
        wl_picks.sort(key=lambda x:x["score"],reverse=True)
        wl_picks = wl_picks[:3]
        if bearish: wl_picks=[s for s in wl_picks if "BUY" not in s["label"]]

        # Phase 3 — penny (batch 1mo)
        log.info("Downloading penny stocks...")
        peh = batch_dl(PENNY_LIST, "1mo")
        penny = []
        for sym in PENNY_LIST:
            r = score_penny(sym, peh.get(sym), max_price=PENNY_MAX_USD)
            if r: penny.append(r)
        penny.sort(key=lambda x:x["score"],reverse=True)
        penny = penny[:5]

        with _lock:
            _cache["us"].update({"watchlist_picks":wl_picks,"penny_picks":penny,
                                  "last_updated":datetime.now().isoformat(),"scanning":False})
        log.info(f"=== US scan done: {len(signals)} portfolio / {len(wl_picks)} watch / {len(penny)} penny ===")

    except Exception as e:
        log.error(f"US scan crashed: {e}", exc_info=True)
        with _lock: _cache["us"]["scanning"]=False; _cache["us"]["error"]=str(e)

def run_india_scan():
    log.info("=== India scan start ===")
    with _lock: _cache["india"]["scanning"]=True; _cache["india"]["error"]=None
    try:
        nifty_pct, bearish = market_mood("NIFTYBEES.NS")
        cfg  = read_config()
        port = cfg.get("india_portfolio", INDIA_PORTFOLIO)

        log.info(f"Downloading {len(port)} India portfolio stocks...")
        ph = batch_dl(port, "1y", ns=True)
        signals = []
        for sym in port:
            r = score(sym, ph.get(sym), ns=True)
            if r:
                if bearish and "BUY" in r["label"]:
                    r["label"]="HOLD"; r["why"]=f"Suppressed — NIFTY down {nifty_pct}%"
                r = build_signal(r)
                signals.append(r)
                log.info(f"  {sym}: {r['label']}")
            else:
                log.warning(f"  {sym}: no signal")

        with _lock:
            _cache["india"].update({"signals":signals,"nifty_pct":nifty_pct,"market_bearish":bearish,
                                     "last_updated":datetime.now().isoformat(),"scanning":True})
        log.info(f"India portfolio done ({len(signals)}). Downloading watchlist...")

        wh = batch_dl(INDIA_WATCHLIST, "1y", ns=True)
        wl_picks = []
        for sym in INDIA_WATCHLIST:
            r = score(sym, wh.get(sym), ns=True)
            if r and r["score"]>=2: wl_picks.append(build_signal(r))
        wl_picks.sort(key=lambda x:x["score"],reverse=True)
        wl_picks = wl_picks[:3]
        if bearish: wl_picks=[s for s in wl_picks if "BUY" not in s["label"]]

        # Phase 3 — India penny (under ₹30)
        log.info("Downloading India penny stocks...")
        iph = batch_dl(INDIA_PENNY_LIST, "1mo", ns=True)
        india_penny = []
        for sym in INDIA_PENNY_LIST:
            r = score_penny(sym, iph.get(sym), max_price=PENNY_MAX_INR)
            if r: india_penny.append(r)
        india_penny.sort(key=lambda x:x["score"],reverse=True)
        india_penny = india_penny[:5]

        with _lock:
            _cache["india"].update({"watchlist_picks":wl_picks,"penny_picks":india_penny,
                                     "last_updated":datetime.now().isoformat(),"scanning":False})
        log.info(f"=== India scan done: {len(signals)} portfolio / {len(wl_picks)} watch / {len(india_penny)} penny ===")

    except Exception as e:
        log.error(f"India scan crashed: {e}", exc_info=True)
        with _lock: _cache["india"]["scanning"]=False; _cache["india"]["error"]=str(e)

def run_top_picks_scan():
    """Scan portfolio + curated large-caps, return top 5 BUY signals (US only)."""
    log.info("=== Top picks scan start ===")
    try:
        spy_pct, bearish = market_mood("SPY")
        cfg  = read_config()
        port = cfg.get("us_portfolio", US_PORTFOLIO)

        # Combine portfolio + curated large-caps, dedupe (preserve order)
        seen = set()
        universe = []
        for sym in list(port) + LARGECAP_WATCHLIST:
            if sym not in seen:
                seen.add(sym); universe.append(sym)

        if bearish:
            result = {"suppressed":True,
                      "reason":"Market is bearish — BUY signals suppressed to protect capital",
                      "picks":[], "last_updated":datetime.now().isoformat()}
            with _lock: _top_picks_cache["us"] = result
            log.info("Top picks suppressed — market bearish")
            return result

        log.info(f"Top picks: scoring {len(universe)} tickers...")
        hist = batch_dl(universe, "1y")
        buys = []
        for sym in universe:
            r = score(sym, hist.get(sym))
            if r and "BUY" in r["label"]:
                buys.append(build_signal(r))
        buys.sort(key=lambda x:x["score"], reverse=True)
        picks = buys[:5]

        result = {"suppressed":False, "reason":None, "picks":picks,
                  "last_updated":datetime.now().isoformat()}
        with _lock: _top_picks_cache["us"] = result
        log.info(f"=== Top picks scan done: {len(picks)} picks ===")
        return result
    except Exception as e:
        log.error(f"Top picks scan crashed: {e}", exc_info=True)
        return None

def background_loop():
    while True:
        try:
            run_us_scan()
            run_top_picks_scan()
            run_india_scan()
            log.info("Both scans done. Sleeping 30 min...")
        except Exception as e:
            log.error(f"Scan cycle crashed, server stays up: {e}", exc_info=True)
        time.sleep(30*60)

# ── Live quote fetcher (for price cards) ─────────────────────────
def live_quotes(symbols):
    results = []
    try:
        tickers = yf.Tickers(" ".join(symbols))
        for sym in symbols:
            try:
                fi    = tickers.tickers[sym].fast_info
                prev  = fi.previous_close or 0
                price = fi.last_price or 0
                chg   = price-prev; pct = (chg/prev*100) if prev else 0
                results.append({"symbol":sym,"regularMarketPrice":round(price,2),
                    "regularMarketChange":round(chg,2),"regularMarketChangePercent":round(pct,2),
                    "regularMarketOpen":round(fi.open or 0,2),"regularMarketDayHigh":round(fi.day_high or 0,2),
                    "regularMarketDayLow":round(fi.day_low or 0,2),"regularMarketPreviousClose":round(prev,2),"shortName":sym})
            except Exception as e:
                results.append({"symbol":sym,"error":str(e)})
    except Exception as e:
        log.error(f"live_quotes error: {e}")
    return results

# ── HTTP Handler ──────────────────────────────────────────────────
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw): super().__init__(*a,directory=DIR,**kw)

    def do_GET(self):
        p = urlparse(self.path)
        if   p.path=="/api/signals": self._signals(p)
        elif p.path=="/api/top-picks": self._top_picks(p)
        elif p.path=="/api/quotes":  self._quotes(p)
        elif p.path=="/api/config":  self._json(200,read_config())
        elif p.path=="/api/scan":    self._trigger_scan(p)
        elif p.path=="/api/notify-test": self._test_notify()
        else: super().do_GET()

    def do_POST(self):
        p = urlparse(self.path)
        if   p.path=="/api/config": self._save_config()
        elif p.path=="/api/scan":   self._trigger_scan(p)
        else: self._json(404,{"error":"not found"})

    def do_OPTIONS(self):
        self.send_response(200)
        for h,v in [("Access-Control-Allow-Origin","*"),("Access-Control-Allow-Methods","GET,POST,OPTIONS"),("Access-Control-Allow-Headers","Content-Type")]:
            self.send_header(h,v)
        self.end_headers()

    def _signals(self,p):
        mkt = parse_qs(p.query).get("market",["us"])[0].lower()
        if mkt not in ("us","india"): self._json(400,{"error":"invalid market"}); return
        with _lock: data=dict(_cache[mkt])
        self._json(200,data)

    def _top_picks(self,p):
        mkt = parse_qs(p.query).get("market",["us"])[0].lower()
        if mkt != "us": self._json(400,{"error":"only US market supported"}); return
        with _lock: cached = dict(_top_picks_cache["us"])
        fresh = False
        if cached.get("last_updated"):
            try:
                age = (datetime.now()-datetime.fromisoformat(cached["last_updated"])).total_seconds()
                fresh = age < 30*60
            except Exception:
                fresh = False
        if not fresh:
            result = run_top_picks_scan()
            if result: cached = result
        self._json(200,{"suppressed":cached.get("suppressed",False),
                        "reason":cached.get("reason"),
                        "picks":cached.get("picks",[]),
                        "last_updated":cached.get("last_updated")})

    def _quotes(self,p):
        syms=[s.strip().upper() for s in parse_qs(p.query).get("symbols",[""])[0].split(",") if s.strip()]
        try: self._json(200,{"result":live_quotes(syms)})
        except Exception as e: self._json(500,{"error":str(e)})

    def _trigger_scan(self,p):
        mkt = parse_qs(p.query).get("market",["both"])[0].lower()
        def go():
            if mkt in("us","both"):    run_us_scan()
            if mkt in("india","both"): run_india_scan()
        threading.Thread(target=go,daemon=True).start()
        self._json(200,{"ok":True,"message":f"Scan triggered for {mkt}"})

    def _test_notify(self):
        ok=send_telegram("🤖 <b>McLean Trade Bot</b> — dashboard connected!\n\nSignals update every 30 min.\n<i>Not financial advice.</i>")
        self._json(200,{"ok":ok})

    def _save_config(self):
        try:
            n=int(self.headers.get("Content-Length",0))
            data=json.loads(self.rfile.read(n))
            allowed={"telegram_token","telegram_chat_id","market","us_portfolio","india_portfolio"}
            write_config({k:v for k,v in data.items() if k in allowed})
            self._json(200,{"ok":True})
        except Exception as e:
            self._json(400,{"ok":False,"error":str(e)})

    def _json(self,code,data):
        body=json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Content-Length",len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self,fmt,*args):
        try:
            if args and isinstance(args[0], str) and "/api/" in args[0]:
                print(f"  {args[0]} → {args[1] if len(args)>1 else ''}")
        except Exception:
            pass

if __name__=="__main__":
    port=8080
    print(f"\n✅ McLean Trade Bot starting on port {port}")
    print(f"   http://localhost:{port}/dashboard.html")
    print("   Background scan starting now — portfolio results in ~30 sec\n")
    threading.Thread(target=background_loop,daemon=True).start()
    os.system(f"open http://localhost:{port}/dashboard.html")
    HTTPServer(("",port),Handler).serve_forever()
