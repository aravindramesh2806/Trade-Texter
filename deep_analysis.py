#!/usr/bin/env python3
"""
McLean Trade Bot — Deep Analysis (TradingAgents powered)
=========================================================
Runs a full multi-agent LLM analysis on a single stock and sends the
report to your Telegram.

TradingAgents deploys 6+ specialised AI agents:
  - Fundamentals Analyst (financials, earnings, valuation)
  - Sentiment Analyst    (social media & news mood)
  - News Analyst         (macro events, sector impact)
  - Technical Analyst    (RSI, MACD, EMA, patterns)
  - Bull + Bear Researcher (debate: pros vs cons)
  - Trader Agent         (final BUY / SELL / HOLD + reasoning)

Takes 3–8 minutes per stock. Use for high-stakes decisions, not daily scans.

USAGE:
    python3 deep_analysis.py NVDA
    python3 deep_analysis.py AAPL
    python3 deep_analysis.py TSLA --date 2026-06-01

SETUP (one-time):
    pip3 install tradingagents langchain langgraph
    export ANTHROPIC_API_KEY="your-key-here"        # from console.anthropic.com
    # OR
    export OPENAI_API_KEY="your-key-here"           # from platform.openai.com
"""
import sys, os, time, logging, urllib.request, urllib.parse
from datetime import date

# ── Config ────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = "YOUR_BOT_TOKEN_HERE"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID_HERE"

# LLM provider — change to "openai" if you have OpenAI key instead
LLM_PROVIDER  = "anthropic"
DEEP_MODEL    = "claude-sonnet-4-6"   # used for complex reasoning steps
QUICK_MODEL   = "claude-haiku-4-5-20251001"  # used for quick classification steps
MAX_DEBATE    = 1                             # debate rounds (1 = fast, 2 = thorough)

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deep_analysis.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("deep")
# ─────────────────────────────────────────────────────────────────


def send_telegram(message: str, retries: int = 3) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for attempt in range(1, retries + 1):
        try:
            data = urllib.parse.urlencode({
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       message,
                "parse_mode": "HTML",
            }).encode()
            urllib.request.urlopen(url, data=data, timeout=20)
            log.info("Telegram: sent successfully")
            return True
        except Exception as e:
            wait = 2 ** attempt
            log.warning(f"Telegram attempt {attempt}/{retries} failed: {e}. Retry in {wait}s")
            if attempt < retries:
                time.sleep(wait)
    log.error("All Telegram retries failed.")
    return False


def check_api_key() -> str:
    """Returns the active LLM provider name, or raises if no key found."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("GOOGLE_API_KEY"):
        return "google"
    raise EnvironmentError(
        "No LLM API key found.\n"
        "Set one of:\n"
        "  export ANTHROPIC_API_KEY='sk-ant-...'   (from console.anthropic.com)\n"
        "  export OPENAI_API_KEY='sk-...'          (from platform.openai.com)\n"
    )


def run_analysis(ticker: str, analysis_date: str) -> dict:
    """Run TradingAgents on a single ticker. Returns the decision dict."""
    try:
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        from tradingagents.default_config import DEFAULT_CONFIG
    except ImportError:
        raise ImportError(
            "TradingAgents not installed.\n"
            "Run: pip3 install tradingagents langchain langgraph\n"
        )

    provider = check_api_key()

    # Map provider to model names
    model_map = {
        "anthropic": ("claude-sonnet-4-6", "claude-haiku-4-5-20251001"),
        "openai":    ("gpt-4o",            "gpt-4o-mini"),
        "google":    ("gemini-2.0-flash",  "gemini-2.0-flash"),
    }
    deep_m, quick_m = model_map.get(provider, (DEEP_MODEL, QUICK_MODEL))

    config = DEFAULT_CONFIG.copy()
    config["llm_provider"]     = provider
    config["deep_think_llm"]   = deep_m
    config["quick_think_llm"]  = quick_m
    config["max_debate_rounds"] = MAX_DEBATE

    log.info(f"Running TradingAgents on {ticker} ({analysis_date}) — provider={provider}")
    log.info("This takes 3–8 minutes. Please wait...")

    ta = TradingAgentsGraph(debug=False, config=config)
    state, decision = ta.propagate(ticker, analysis_date)
    return decision


def format_telegram_message(ticker: str, analysis_date: str, decision: dict) -> str:
    """Format the TradingAgents decision as a clean Telegram HTML message."""
    action   = decision.get("action", "UNKNOWN").upper()
    reason   = decision.get("reason", "")
    risk     = decision.get("risk_level", "")
    confidence = decision.get("confidence", "")

    # Action emoji-free label
    action_label = {
        "BUY":       "BUY",
        "SELL":      "SELL",
        "HOLD":      "HOLD",
        "STRONG BUY":  "STRONG BUY",
        "STRONG SELL": "STRONG SELL",
    }.get(action, action)

    lines = [
        f"<b>DEEP ANALYSIS — {ticker}</b>",
        f"Date: {analysis_date}",
        "",
        f"Decision: <b>{action_label}</b>",
    ]

    if confidence:
        lines.append(f"Confidence: {confidence}")
    if risk:
        lines.append(f"Risk Level: {risk}")

    if reason:
        # Truncate to Telegram's 4096 char limit
        reason_trimmed = reason[:800] + ("..." if len(reason) > 800 else "")
        lines += ["", "<b>Analysis:</b>", reason_trimmed]

    lines += [
        "",
        "<i>TradingAgents · McLean Trade Bot · Not financial advice</i>",
    ]
    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Deep LLM analysis for a single stock")
    parser.add_argument("ticker", help="Stock ticker symbol, e.g. NVDA")
    parser.add_argument("--date", default=str(date.today()), help="Analysis date YYYY-MM-DD")
    args = parser.parse_args()

    ticker        = args.ticker.upper().strip()
    analysis_date = args.date

    # Send "running" notification
    send_telegram(
        f"<b>Deep Analysis Started</b>\n"
        f"Ticker: <b>{ticker}</b>\n"
        f"Date: {analysis_date}\n"
        f"Running 6-agent LLM analysis... ETA 3–8 min"
    )

    try:
        decision = run_analysis(ticker, analysis_date)
        msg      = format_telegram_message(ticker, analysis_date, decision)
        send_telegram(msg)
        log.info(f"Deep analysis complete for {ticker}: {decision.get('action')}")

    except EnvironmentError as e:
        err = f"Deep Analysis FAILED — API key missing\n\n{e}"
        log.error(err)
        send_telegram(f"<b>Deep Analysis Error</b>\n{str(e)[:400]}")
        sys.exit(1)

    except ImportError as e:
        err = f"Deep Analysis FAILED — TradingAgents not installed\n\n{e}"
        log.error(err)
        send_telegram(f"<b>Deep Analysis Error</b>\n{str(e)[:400]}")
        sys.exit(1)

    except Exception as e:
        log.error(f"Deep analysis error: {e}")
        send_telegram(f"<b>Deep Analysis Error</b>\n{ticker}: {str(e)[:400]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
