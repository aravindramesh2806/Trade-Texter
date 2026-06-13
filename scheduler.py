#!/usr/bin/env python3
"""
Sends the McLean Trade Bot morning briefing at 8:30 AM ET every weekday.
Runs as a standalone daemon alongside server.py.
No external dependencies — stdlib only (time, datetime, json, subprocess).
"""
import time, json, os, subprocess, sys
from datetime import datetime
import importlib.util

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

def should_send(now):
    """Return True if it's a weekday and exactly 8:30 AM ET (checked each minute)."""
    # Use ET offset: EST = UTC-5, EDT = UTC-4
    try:
        import pytz
        et = pytz.timezone("US/Eastern")
        now_et = datetime.now(et)
    except ImportError:
        # Fallback: assume UTC-4 (EDT)
        from datetime import timezone, timedelta
        now_et = datetime.now(timezone(timedelta(hours=-4)))
    return now_et.weekday() < 5 and now_et.hour == 8 and now_et.minute == 30

def run_briefing():
    """Import and run telegram_alert main logic directly."""
    try:
        # Import send_alert from telegram_alert.py
        spec = importlib.util.spec_from_file_location(
            "telegram_alert",
            os.path.join(os.path.dirname(__file__), "telegram_alert.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "main"):
            mod.main()
        elif hasattr(mod, "send_alert"):
            mod.send_alert()
        else:
            # Fallback: run as subprocess
            subprocess.run([sys.executable, "telegram_alert.py"], check=False)
        print(f"[scheduler] Briefing sent at {datetime.now().isoformat()}")
    except Exception as e:
        print(f"[scheduler] Error sending briefing: {e}")

if __name__ == "__main__":
    print("[scheduler] Started. Will send briefing at 8:30 AM ET on weekdays.")
    sent_today = None
    while True:
        now = datetime.now()
        today = now.date()
        if should_send(now) and sent_today != today:
            token, chat = load_creds()
            if token and chat:
                run_briefing()
                sent_today = today
            else:
                print("[scheduler] No credentials found — skipping.")
        time.sleep(60)
