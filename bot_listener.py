#!/usr/bin/env python3
"""
Listens for Telegram messages and responds to /scan with a fresh market report.
Uses long-polling — no webhook needed.
"""
import time, json, os, requests, importlib.util
from datetime import datetime

def load_creds():
    token = os.environ.get("TELEGRAM_BOT_TOKEN","")
    chat  = os.environ.get("TELEGRAM_CHAT_ID","")
    if not token or not chat:
        try:
            cfg = json.load(open(os.path.join(os.path.dirname(__file__),"config.json")))
            token = token or cfg.get("TELEGRAM_BOT_TOKEN","")
            chat  = chat  or cfg.get("TELEGRAM_CHAT_ID","")
        except Exception:
            pass
    return token, chat

def send_message(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print(f"[bot] send error: {e}")

def get_updates(token, offset, timeout=9):
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        r = requests.get(url, params={"offset": offset, "timeout": timeout}, timeout=timeout+5)
        return r.json().get("result", [])
    except Exception:
        return []

def run_scan_and_get_message():
    """Import telegram_alert, run a fresh scan, and return the formatted report."""
    try:
        spec = importlib.util.spec_from_file_location(
            "telegram_alert",
            os.path.join(os.path.dirname(__file__), "telegram_alert.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # fetch_signals() returns the full data bundle; build_message() formats it.
        if hasattr(mod, "fetch_signals") and hasattr(mod, "build_message"):
            data = mod.fetch_signals()
            if not data.get("signals"):
                return "⚠️ Scan complete but no valid signals returned. Try again shortly."
            return mod.build_message(
                data["signals"],
                data.get("penny_picks"),
                data.get("watchlist_picks"),
                data.get("spy_pct"),
                data.get("market_bearish", False),
            )
        elif hasattr(mod, "get_report"):
            return mod.get_report()
        else:
            return "✅ Scan complete — check the dashboard at http://localhost:8080/dashboard.html"
    except Exception as e:
        return f"⚠️ Scan error: {e}"

if __name__ == "__main__":
    token, chat_id = load_creds()
    if not token:
        print("[bot] No TELEGRAM_BOT_TOKEN found — exiting.")
        exit(0)

    print(f"[bot] Listening for /scan commands...")
    offset = 0
    while True:
        updates = get_updates(token, offset)
        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            text = msg.get("text", "").strip().lower()
            chat = msg.get("chat", {}).get("id")
            if not chat:
                continue
            if "scan" in text:
                send_message(token, chat, "🔍 Running fresh scan... please wait ~30 seconds.")
                report = run_scan_and_get_message()
                send_message(token, chat, report)
                print(f"[bot] Scan report sent to {chat} at {datetime.now().isoformat()}")
            else:
                send_message(token, chat, "Send /scan for a fresh market report.")
        time.sleep(1)
