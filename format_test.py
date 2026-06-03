#!/usr/bin/env python3
import urllib.request, urllib.parse, time

TOKEN   = "8955387419:AAHEmmHoibcYkv2MRcFElzX__4TOrP55PjQ"
CHAT_ID = "1578063059"

def send(msg):
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"
    }).encode()
    urllib.request.urlopen(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=data, timeout=10
    )
    time.sleep(2)

# ── OPTION A: Clean card style ───────────────────────────────────
send("""
<b>━━━ OPTION A: Card Style ━━━</b>
""")

send("""📊 <b>McLean Trade Bot</b> · Mon Jun 1 · 7:00 AM

<b>Market:</b> 🟢 Bullish  |  <b>Signals:</b> 3 BUY · 4 HOLD · 0 SELL

──────────────────
<b>🔥 ACT NOW</b>
──────────────────
🟢 <b>GOOGL</b>  $376  ▼1.0%
    └ <b>BUY</b> · RSI 40 oversold · MACD turning up

🟢 <b>AMZN</b>   $261  ▼3.5%
    └ <b>BUY</b> · RSI 43 · Dip into support zone

──────────────────
<b>📋 FULL PORTFOLIO</b>
──────────────────
⬜ AAPL  $306  ▼1.8%  HOLD
🟢 GOOGL $376  ▼1.0%  BUY
⬜ PLTR  $160  ▲2.6%  HOLD  ⚠️ RSI 75
⬜ VOO   $697  ▲0.3%  HOLD
⬜ NVDA  $224  ▲6.3%  HOLD
🟢 AMD   $510  ▼1.2%  BUY
🟢 AMZN  $261  ▼3.5%  BUY
⬜ CRM   $209  ▲9.7%  HOLD ⚠️ RSI 73

──────────────────
<b>💎 PENNY PICKS</b>
──────────────────
🔥 <b>GFAI</b>  $0.60  ▲13.2%  Vol 3.7x  Score 6/8
📈 <b>BTBT</b>  $2.11  ▲4.5%   Vol 1.2x  Score 3/8

<i>⚠️ Not financial advice</i>""")

# ── OPTION B: Minimal table ──────────────────────────────────────
send("""
<b>━━━ OPTION B: Minimal ━━━</b>
""")

send("""🤖 <b>Daily Alerts · Jun 1 · 7AM</b>

<code>TICKER  PRICE    DAY    SIGNAL
──────  ──────  ─────  ───────
AAPL    $306   -1.8%  HOLD
GOOGL   $376   -1.0%  BUY ✅
PLTR    $160   +2.6%  HOLD
VOO     $697   +0.3%  HOLD
NVDA    $224   +6.3%  HOLD
AMD     $510   -1.2%  BUY ✅
AMZN    $261   -3.5%  BUY ✅
CRM     $209   +9.7%  HOLD</code>

💎 <b>Penny:</b> GFAI $0.60 ▲13% · BTBT $2.11 ▲4.5%
🌍 <b>Macro:</b> COMPUTEX AI rally · Fed on hold · NVDA China risk

<i>Not financial advice</i>""")

# ── OPTION C: Emoji-rich visual ──────────────────────────────────
send("""
<b>━━━ OPTION C: Emoji Visual ━━━</b>
""")

send("""🔔 <b>TRADE ALERT</b> · Mon 1 Jun · Pre-Market

╔══ 🔥 BUY THESE TODAY ══╗
║  📈 GOOGL $376 (-1%) — oversold RSI
║  📈 AMD   $510 (-1%) — bullish MACD
║  📈 AMZN  $261 (-3.5%) — near support
╚═══════════════════════╝

📊 <b>Watchlist:</b>
🟢 GOOGL · 🟢 AMD · 🟢 AMZN
🟡 NVDA · 🟡 VOO · 🟡 CRM
🔴 AAPL (RSI 71) · 🔴 PLTR (RSI 75)

💎 <b>Penny Rocket:</b>
🚀 GFAI $0.60 · +13% · Vol 3.7x · <b>MOMENTUM BUY</b>

<i>⚠️ High risk. Small size. Not advice.</i>""")

print("All 3 options sent to Telegram!")
