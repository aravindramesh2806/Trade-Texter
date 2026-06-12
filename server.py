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
from datetime import datetime, timedelta
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
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

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

def calc_adx(hist, p=14):
    """Average Directional Index — measures trend STRENGTH (not direction).
    Used to detect range-bound/choppy conditions where MACD/RSI signals
    are statistically less reliable (more whipsaws)."""
    if len(hist) < p*2: return None
    h,l,c = hist["High"], hist["Low"], hist["Close"]
    up   = h.diff()
    down = -l.diff()
    plus_dm  = np.where((up>down)&(up>0), up, 0.0)
    minus_dm = np.where((down>up)&(down>0), down, 0.0)
    pc = c.shift(1)
    tr = pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1)
    atr = tr.rolling(p).mean()
    plus_di  = 100 * pd.Series(plus_dm,  index=h.index).rolling(p).mean() / atr.replace(0,np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=h.index).rolling(p).mean() / atr.replace(0,np.nan)
    denom = (plus_di+minus_di).replace(0,np.nan)
    dx = 100 * (plus_di-minus_di).abs() / denom
    adx = dx.rolling(p).mean()
    return sf(adx.iloc[-1])

def calc_weekly_trend(hist):
    """Resample daily closes to weekly and compare price to its 10-week EMA.
    Used for multi-timeframe confirmation — a daily BUY against a weekly
    downtrend (or vice versa) is lower-conviction than one that agrees."""
    try:
        if len(hist) < 70: return None
        wk = hist["Close"].resample("W").last().dropna()
        if len(wk) < 10: return None
        ema10 = wk.ewm(span=10, adjust=False).mean()
        price, ema = sf(wk.iloc[-1]), sf(ema10.iloc[-1])
        if price is None or ema is None: return None
        return "up" if price > ema else "down"
    except Exception:
        return None

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
def score(sym, hist, ns=False, check_recency=True):
    try:
        if hist is None or hist.empty or len(hist) < 30: return None
        last = hist.index[-1]
        if hasattr(last,"tzinfo") and last.tzinfo:
            last = last.replace(tzinfo=None)
        if check_recency and (datetime.now()-last).days > 5: return None

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
        adx     = calc_adx(hist)
        weekly_trend = calc_weekly_trend(hist)
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

        # Range-bound filter — RSI/MACD signals are statistically less
        # reliable when there's no real trend (low ADX). Dampen the score
        # toward HOLD rather than firing a full BUY/SELL into chop.
        if adx is not None and adx < 20 and abs(sc) >= 2:
            sc += -1 if sc > 0 else 1
            rs.append(f"ADX {adx:.0f} — range-bound, signal dampened")

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
            "adx":round(adx,1) if adx else None,
            "weekly_trend":weekly_trend,
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

# ── Fundamentals (name, market cap, P/E, dividend yield, volume) ──
# Cache fundamentals for a while — Yahoo aggressively rate-limits the
# quoteSummary endpoint, and this data barely changes within a scan cycle.
_FUND_CACHE = {}
_FUND_CACHE_TTL = 20 * 60  # seconds

def get_fundamentals_batch(symbols, ns=False, timeout=8):
    """Fetch lightweight company info for a small set of symbols in parallel."""
    if not symbols: return {}
    fetch_map = {s: (s+".NS" if ns and not s.endswith(".NS") else s) for s in symbols}
    out = {}
    now = time.time()
    to_fetch = {}
    for sym, tsym in fetch_map.items():
        cached = _FUND_CACHE.get(tsym)
        if cached and (now - cached[0]) < _FUND_CACHE_TTL:
            out[sym] = cached[1]
        else:
            to_fetch[sym] = tsym

    def _one(sym, tsym):
        for attempt in range(2):
            try:
                # Each call gets its own curl_cffi session — sharing one
                # session across threads causes intermittent
                # "argument of type 'NoneType' is not a container" errors
                # inside yfinance's get_info().
                try:
                    from curl_cffi import requests as cc_requests
                    sess = cc_requests.Session(impersonate="chrome")
                    t = yf.Ticker(tsym, session=sess)
                except Exception:
                    t = yf.Ticker(tsym)
                info = t.get_info()
                return sym, {
                    "name":      info.get("shortName") or info.get("longName") or sym,
                    "exchange":  info.get("exchange") or ("NSE" if ns else ""),
                    "market_cap": info.get("marketCap"),
                    "pe_ratio":  info.get("trailingPE"),
                    "div_yield": info.get("dividendYield"),
                    "volume":    info.get("volume") or info.get("regularMarketVolume"),
                    "prev_close": info.get("previousClose"),
                    "sector":    info.get("sector"),
                }
            except Exception as e:
                if attempt == 0:
                    time.sleep(1.5)
                    continue
                log.warning(f"fundamentals {sym}: {type(e).__name__}: {e}")
                return sym, None

    if to_fetch:
        with ThreadPoolExecutor(max_workers=min(3, len(to_fetch))) as ex:
            futures = {ex.submit(_one, sym, ts): sym for sym, ts in to_fetch.items()}
            for fut in as_completed(futures, timeout=timeout * len(to_fetch) * 2):
                try:
                    sym, data = fut.result(timeout=timeout * 2)
                    if data:
                        out[sym] = data
                        _FUND_CACHE[fetch_map[sym]] = (now, data)
                except Exception as e:
                    log.warning(f"fundamentals future error: {e}")
    log.info(f"Fundamentals: {len(out)}/{len(symbols)} fetched ({len(symbols)-len(to_fetch)} cached)")
    return out

# ── News headlines + earnings date (free, no API key — yfinance) ──
_NEWS_CACHE = {}
_NEWS_CACHE_TTL = 30 * 60  # seconds

def get_news_earnings_batch(symbols, ns=False, timeout=8):
    """Fetch top headlines + next earnings date for a small set of symbols."""
    if not symbols: return {}
    fetch_map = {s: (s+".NS" if ns and not s.endswith(".NS") else s) for s in symbols}
    out = {}
    now = time.time()
    to_fetch = {}
    for sym, tsym in fetch_map.items():
        cached = _NEWS_CACHE.get(tsym)
        if cached and (now - cached[0]) < _NEWS_CACHE_TTL:
            out[sym] = cached[1]
        else:
            to_fetch[sym] = tsym

    def _one(sym, tsym):
        try:
            t = yf.Ticker(tsym)
            headlines = []
            try:
                for n in (t.news or [])[:3]:
                    content = n.get("content", n) if isinstance(n, dict) else {}
                    title = content.get("title") or n.get("title")
                    if title: headlines.append(title)
            except Exception:
                pass
            earnings_date = None
            try:
                cal = t.calendar
                if isinstance(cal, dict):
                    ed = cal.get("Earnings Date")
                    if isinstance(ed, (list, tuple)) and ed:
                        earnings_date = str(ed[0])
                    elif ed:
                        earnings_date = str(ed)
            except Exception:
                pass
            return sym, {"headlines": headlines, "earnings_date": earnings_date}
        except Exception as e:
            log.debug(f"news/earnings {sym}: {e}")
            return sym, {"headlines": [], "earnings_date": None}

    if to_fetch:
        with ThreadPoolExecutor(max_workers=min(4, len(to_fetch))) as ex:
            futures = {ex.submit(_one, sym, ts): sym for sym, ts in to_fetch.items()}
            for fut in as_completed(futures, timeout=timeout * len(to_fetch) * 2):
                try:
                    sym, data = fut.result(timeout=timeout * 2)
                    out[sym] = data
                    _NEWS_CACHE[fetch_map[sym]] = (now, data)
                except Exception as e:
                    log.debug(f"news future error: {e}")
    return out

# ── Reddit "buzz" via ApeWisdom — free, keyless public API ─────────
# Covers ~30 popular subreddits (wallstreetbets, stocks, investing, etc.)
# Only useful for US tickers.
_REDDIT_CACHE = {"data": None, "ts": 0}
_REDDIT_CACHE_TTL = 30 * 60  # seconds

def get_reddit_buzz():
    now = time.time()
    if _REDDIT_CACHE["data"] is not None and (now - _REDDIT_CACHE["ts"]) < _REDDIT_CACHE_TTL:
        return _REDDIT_CACHE["data"]
    out = {}
    try:
        for page in (1, 2):
            url = f"https://apewisdom.io/api/v1.0/filter/all-stocks/page/{page}"
            with urllib.request.urlopen(url, timeout=8) as resp:
                data = json.loads(resp.read().decode())
            for item in data.get("results", []):
                tk = item.get("ticker")
                if tk:
                    out[tk] = {
                        "mentions": item.get("mentions"),
                        "rank": item.get("rank"),
                        "mentions_24h_ago": item.get("mentions_24h_ago"),
                    }
        _REDDIT_CACHE["data"] = out
        _REDDIT_CACHE["ts"] = now
        log.info(f"Reddit buzz: {len(out)} tickers")
    except Exception as e:
        log.debug(f"reddit buzz: {e}")
    return _REDDIT_CACHE["data"] or {}

# ── Plain-English narrative for "what's happening" ────────────────
def generate_narrative(s, mkt_ctx=None):
    sym    = s.get("symbol","")
    name   = s.get("name") or sym
    pct    = s.get("pct",0) or 0
    reasons= s.get("reasons", [])
    label  = s.get("label","HOLD")

    if pct > 0.05:   direction = f"is up {pct:.1f}% today"
    elif pct < -0.05: direction = f"is down {abs(pct):.1f}% today"
    else:            direction = "is roughly flat today"

    parts = [f"{name} ({sym}) {direction}."]
    if reasons:
        parts.append("What's driving it: " + ", ".join(reasons[:2]) + ".")

    if mkt_ctx:
        idx_name, idx_pct, bearish = mkt_ctx
        if idx_pct is not None:
            if bearish:
                parts.append(f"Broader market is weak — {idx_name} {idx_pct:+.1f}% — so signals are being read cautiously.")
            elif idx_pct >= 0.5:
                parts.append(f"It's also riding a broadly strong tape ({idx_name} {idx_pct:+.1f}%).")
            elif idx_pct <= -0.5:
                parts.append(f"That's despite a soft overall market ({idx_name} {idx_pct:+.1f}%).")

    if "STRONG BUY" in label: parts.append("Setup: strong buy signal.")
    elif "BUY" in label:      parts.append("Setup: leans buy.")
    elif "STRONG SELL" in label: parts.append("Setup: strong sell signal.")
    elif "SELL" in label:     parts.append("Setup: leans sell.")
    else:                     parts.append("Setup: no clear edge — hold/watch.")

    return " ".join(parts)

# ── Richer signal schema ──────────────────────────────────────────
def build_signal(s, fundamentals=None, mkt_ctx=None, prev=None, extra=None):
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

    # expected target dates — ATR is the average daily price move, so
    # distance / ATR ≈ how many trading days it'd take to cover that move
    # at the recent pace. This is a rough ETA, not a prediction — capped
    # at 60 trading days so flat/illiquid stocks don't produce silly dates.
    def _eta(target_price):
        if not atr or atr <= 0: return None, None
        dist = abs(target_price - price)
        trading_days = max(1, min(60, dist / atr))
        calendar_days = int(round(trading_days * 7 / 5))
        eta = int(round(trading_days))
        date_str = (datetime.now() + timedelta(days=calendar_days)).strftime("%Y-%m-%d")
        return eta, date_str

    eta_1_days, target_1_date = _eta(target_1)
    eta_2_days, target_2_date = _eta(target_2)

    # position sizing — risk-based. risk_per_share is how much you lose per
    # share if stopped out; shares_per_100_risk is how many shares that buys
    # you per $100 of risk capital (scale linearly for your own risk budget).
    risk_per_share = round(abs(safe_price - stop_loss), 2) if stop_loss else None
    shares_per_100_risk = int(100 // risk_per_share) if risk_per_share and risk_per_share > 0 else None

    # multi-timeframe confirmation — does the weekly trend agree with today's
    # daily signal, or is this a counter-trend bounce/pullback?
    weekly_trend = s.get("weekly_trend")
    mtf_aligned, mtf_note = None, None
    if weekly_trend:
        if "BUY" in label:
            mtf_aligned = (weekly_trend == "up")
            mtf_note = ("Weekly trend also up — daily signal confirmed" if mtf_aligned
                         else "Weekly trend is down — daily BUY may be a bounce, lower conviction")
        elif "SELL" in label:
            mtf_aligned = (weekly_trend == "down")
            mtf_note = ("Weekly trend also down — daily signal confirmed" if mtf_aligned
                         else "Weekly trend is up — daily SELL may be a pullback, lower conviction")

    # signal-change diff vs the previous scan
    signal_change, score_change = None, None
    if prev:
        score_change = score - prev.get("score", score)
        if prev.get("label") != label:
            signal_change = f"{prev.get('label')} → {label}"

    # beginner note
    if "BUY" in label:
        eta_note = f" (around {eta_1_days} trading days, ~{target_1_date})" if eta_1_days else ""
        beginner_note = (f"Consider entering between {entry_zone}. First target is "
                         f"${target_1:.2f}{eta_note}. Exit if price falls below ${stop_loss:.2f}.")
    elif "SELL" in label:
        beginner_note = (f"This stock is showing weakness. If you hold it, consider "
                         f"setting a stop at ${stop_loss:.2f}.")
    else:
        beginner_note = (f"No clear signal right now. Watch for price to break above "
                         f"${resistance:.2f} or below ${support:.2f} before acting.")

    fundamentals = fundamentals or {}
    extra = extra or {}
    merged = {**s, **fundamentals}

    return {
        **s,
        **fundamentals,
        **extra,
        "ticker": s.get("symbol", ""),
        "signal": label,
        "setup_type": setup_type,
        "risk_band": risk_band,
        "confidence": confidence,
        "entry_zone": entry_zone,
        "target_1": target_1,
        "target_2": target_2,
        "target_1_eta_days": eta_1_days,
        "target_1_date": target_1_date,
        "target_2_eta_days": eta_2_days,
        "target_2_date": target_2_date,
        "invalidation": stop_loss,
        "risk_per_share": risk_per_share,
        "shares_per_100_risk": shares_per_100_risk,
        "mtf_aligned": mtf_aligned,
        "mtf_note": mtf_note,
        "signal_change": signal_change,
        "score_change": score_change,
        "beginner_note": beginner_note,
        "narrative": generate_narrative(merged, mkt_ctx),
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
# Previous scan's {symbol: {"label":..., "score":...}} — used to compute
# signal-change diffs (e.g. "HOLD → BUY") between consecutive scans.
_PREV_SIGNALS = {"us": {}, "india": {}}

def _sector_exposure(signals):
    """% of BUY signals concentrated in each sector, plus a concentration warning."""
    buys = [s for s in signals if "BUY" in s.get("label","")]
    counts = {}
    for s in buys:
        sec = s.get("sector") or "Unknown"
        counts[sec] = counts.get(sec, 0) + 1
    total = len(buys)
    pct = {k: round(v/total*100,1) for k,v in counts.items()} if total else {}
    warning = None
    if total >= 2:
        for sec, p in pct.items():
            if p > 50 and sec != "Unknown":
                warning = f"{p:.0f}% of current BUY signals are in {sec} — consider diversifying"
                break
    return {"sector_exposure": pct, "sector_concentration_warning": warning}

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
        mkt_ctx = ("S&P 500", spy_pct, bearish)
        fund = get_fundamentals_batch(port)
        news = get_news_earnings_batch(port)
        reddit = get_reddit_buzz()
        signals = []
        for sym in port:
            r = score(sym, ph.get(sym))
            if r:
                if bearish and "BUY" in r["label"]:
                    r["label"]="HOLD"; r["why"]=f"Suppressed — SPY down {spy_pct}%"
                prev = _PREV_SIGNALS["us"].get(sym)
                extra = dict(news.get(sym) or {})
                if sym in reddit: extra["reddit"] = reddit[sym]
                r = build_signal(r, fund.get(sym), mkt_ctx, prev=prev, extra=extra)
                signals.append(r)
                _PREV_SIGNALS["us"][sym] = {"label": r["label"], "score": r["score"]}
                log.info(f"  {sym}: {r['label']}  score={r['score']}")
            else:
                log.warning(f"  {sym}: no signal")

        with _lock:
            _cache["us"].update({"signals":signals,"spy_pct":spy_pct,"market_bearish":bearish,
                                  "last_updated":datetime.now().isoformat(),"scanning":True,
                                  **_sector_exposure(signals)})
        log.info(f"Portfolio done ({len(signals)}). Downloading watchlist...")

        # Phase 2 — watchlist (batch)
        wl = cfg.get("us_watchlist", US_WATCHLIST)
        wh = batch_dl(wl, "1y")
        candidates = []
        for sym in wl:
            r = score(sym, wh.get(sym))
            if r and r["score"]>=2: candidates.append(r)
        candidates.sort(key=lambda x:x["score"],reverse=True)
        candidates = candidates[:3]
        wl_fund = get_fundamentals_batch([c["symbol"] for c in candidates])
        wl_picks = [build_signal(c, wl_fund.get(c["symbol"]), mkt_ctx) for c in candidates]
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
        mkt_ctx = ("NIFTY", nifty_pct, bearish)
        fund = get_fundamentals_batch(port, ns=True)
        news = get_news_earnings_batch(port, ns=True)
        signals = []
        for sym in port:
            r = score(sym, ph.get(sym), ns=True)
            if r:
                if bearish and "BUY" in r["label"]:
                    r["label"]="HOLD"; r["why"]=f"Suppressed — NIFTY down {nifty_pct}%"
                prev = _PREV_SIGNALS["india"].get(sym)
                r = build_signal(r, fund.get(sym), mkt_ctx, prev=prev, extra=news.get(sym))
                signals.append(r)
                _PREV_SIGNALS["india"][sym] = {"label": r["label"], "score": r["score"]}
                log.info(f"  {sym}: {r['label']}")
            else:
                log.warning(f"  {sym}: no signal")

        with _lock:
            _cache["india"].update({"signals":signals,"nifty_pct":nifty_pct,"market_bearish":bearish,
                                     "last_updated":datetime.now().isoformat(),"scanning":True,
                                     **_sector_exposure(signals)})
        log.info(f"India portfolio done ({len(signals)}). Downloading watchlist...")

        wh = batch_dl(INDIA_WATCHLIST, "1y", ns=True)
        candidates = []
        for sym in INDIA_WATCHLIST:
            r = score(sym, wh.get(sym), ns=True)
            if r and r["score"]>=2: candidates.append(r)
        candidates.sort(key=lambda x:x["score"],reverse=True)
        candidates = candidates[:3]
        wl_fund = get_fundamentals_batch([c["symbol"] for c in candidates], ns=True)
        wl_picks = [build_signal(c, wl_fund.get(c["symbol"]), mkt_ctx) for c in candidates]
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
                buys.append(r)
        buys.sort(key=lambda x:x["score"], reverse=True)
        top5 = buys[:5]
        mkt_ctx = ("S&P 500", spy_pct, bearish)
        fund = get_fundamentals_batch([b["symbol"] for b in top5])
        picks = [build_signal(b, fund.get(b["symbol"]), mkt_ctx) for b in top5]

        result = {"suppressed":False, "reason":None, "picks":picks,
                  "last_updated":datetime.now().isoformat()}
        with _lock: _top_picks_cache["us"] = result
        log.info(f"=== Top picks scan done: {len(picks)} picks ===")
        return result
    except Exception as e:
        log.error(f"Top picks scan crashed: {e}", exc_info=True)
        return None

# ── Backtest — replay the scoring logic over history ──────────────
def run_backtest(sym, ns=False, period="2y", hold_days=10):
    """Every 5 trading days, compute the signal using only data available up
    to that point, then check the price return `hold_days` later. Reports
    count / avg return / win rate per label. This is a sanity check on the
    algorithm, not a guarantee of future performance."""
    try:
        tsym = sym + ".NS" if ns and not sym.endswith(".NS") else sym
        hist = yf.Ticker(tsym).history(period=period, auto_adjust=True)
        if hist is None or len(hist) < 250:
            return {"error": "not enough history for a meaningful backtest"}
        closes = hist["Close"].values
        results = {"STRONG BUY":[], "BUY":[], "HOLD":[], "SELL":[], "STRONG SELL":[]}
        for i in range(200, len(hist)-hold_days, 5):
            window = hist.iloc[:i+1]
            r = score(sym, window, ns=ns, check_recency=False)
            if not r: continue
            entry, exitp = closes[i], closes[i+hold_days]
            if entry == 0: continue
            ret = (exitp-entry)/entry*100
            results[r["label"]].append(ret)
        summary = {}
        for label, rets in results.items():
            if not rets: continue
            if "BUY" in label:  wins = sum(1 for x in rets if x > 0)
            elif "SELL" in label: wins = sum(1 for x in rets if x < 0)
            else: wins = None
            summary[label] = {
                "count": len(rets),
                "avg_return_pct": round(sum(rets)/len(rets), 2),
                "win_rate_pct": round(wins/len(rets)*100, 1) if wins is not None else None,
            }
        return {"symbol": sym, "period": period, "hold_days": hold_days, "results": summary}
    except Exception as e:
        log.warning(f"backtest {sym}: {e}")
        return {"error": str(e)}

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
# Short TTL cache — quotes don't need to be fetched more than once
# every few seconds even if multiple clients/cards request them.
_QUOTE_CACHE = {}
_QUOTE_CACHE_TTL = 15  # seconds

def live_quotes(symbols):
    now = time.time()
    cached_results = []
    to_fetch = []
    for sym in symbols:
        cached = _QUOTE_CACHE.get(sym)
        if cached and (now - cached[0]) < _QUOTE_CACHE_TTL:
            cached_results.append(cached[1])
        else:
            to_fetch.append(sym)
    if not to_fetch:
        return cached_results

    fetched = _live_quotes_fetch(to_fetch)
    for r in fetched:
        sym = r.get("symbol")
        if sym and "error" not in r:
            _QUOTE_CACHE[sym] = (now, r)
    return cached_results + fetched

def _live_quotes_fetch(symbols):
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

# ── Chart OHLC fetcher ───────────────────────────────────────────
# Short TTL cache — sparklines on the dashboard request the same
# symbol/range repeatedly (top picks, best opportunity, asset cards),
# so caching avoids redundant yfinance round-trips and makes the page
# feel much snappier.
_CHART_CACHE = {}
_CHART_CACHE_TTL = {"1d": 60, "1w": 5*60, "1mo": 15*60, "6mo": 30*60, "1y": 30*60, "5y": 60*60}

def chart_candles(symbol="SPY", rng="1d"):
    key = (symbol, rng)
    ttl = _CHART_CACHE_TTL.get(rng, 60)
    cached = _CHART_CACHE.get(key)
    if cached and (time.time() - cached[0]) < ttl:
        return cached[1]

    params = {
        "1d":  {"period": "1d",  "interval": "5m"},
        "1w":  {"period": "5d",  "interval": "30m"},
        "1mo": {"period": "1mo", "interval": "1d"},
        "6mo": {"period": "6mo", "interval": "1d"},
        "1y":  {"period": "1y",  "interval": "1wk"},
        "5y":  {"period": "5y",  "interval": "1mo"},
    }
    cfg = params.get(rng, params["1d"])
    df = yf.download(symbol, period=cfg["period"], interval=cfg["interval"],
                     auto_adjust=False, progress=False)
    candles = []
    if df is None or df.empty:
        return candles
    # yfinance may return MultiIndex columns when one symbol is requested
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    for idx, row in df.iterrows():
        o, h, l, c = sf(row.get("Open")), sf(row.get("High")), sf(row.get("Low")), sf(row.get("Close"))
        if None in (o, h, l, c):
            continue
        ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
        candles.append({
            "time":  int(ts.timestamp()),
            "open":  round(o, 2),
            "high":  round(h, 2),
            "low":   round(l, 2),
            "close": round(c, 2),
        })
    _CHART_CACHE[key] = (time.time(), candles)
    return candles

# ── HTTP Handler ──────────────────────────────────────────────────
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw): super().__init__(*a,directory=DIR,**kw)

    def do_GET(self):
        p = urlparse(self.path)
        if   p.path=="/api/signals": self._signals(p)
        elif p.path=="/api/top-picks": self._top_picks(p)
        elif p.path=="/api/quotes":  self._quotes(p)
        elif p.path=="/api/chart":   self._chart(p)
        elif p.path=="/api/config":  self._json(200,read_config())
        elif p.path=="/api/scan":    self._trigger_scan(p)
        elif p.path=="/api/backtest": self._backtest(p)
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

    def _chart(self,p):
        q     = parse_qs(p.query)
        sym   = q.get("symbol",["SPY"])[0].strip().upper() or "SPY"
        rng   = q.get("range",["1d"])[0].strip().lower()
        try:
            self._json(200,{"candles":chart_candles(sym,rng)})
        except Exception as e:
            log.warning(f"chart error {sym} {rng}: {e}")
            self._json(500,{"error":str(e),"candles":[]})

    def _backtest(self,p):
        q   = parse_qs(p.query)
        sym = q.get("symbol",[""])[0].strip().upper()
        mkt = q.get("market",["us"])[0].lower()
        hold = q.get("hold_days",["10"])[0]
        if not sym: self._json(400,{"error":"symbol required"}); return
        try: hold_days = max(1, min(30, int(hold)))
        except Exception: hold_days = 10
        result = run_backtest(sym, ns=(mkt=="india"), hold_days=hold_days)
        self._json(200 if "error" not in result else 500, result)

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
    port = int(os.environ.get("PORT", 8080))
    print(f"\n✅ McLean Trade Bot starting on port {port}")
    print(f"   http://localhost:{port}/dashboard.html")
    print("   Background scan starting now — portfolio results in ~30 sec\n")
    threading.Thread(target=background_loop,daemon=True).start()
    HTTPServer(("",port),Handler).serve_forever()
