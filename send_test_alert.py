import urllib.request, urllib.parse

token   = "8955387419:AAHEmmHoibcYkv2MRcFElzX__4TOrP55PjQ"
chat_id = "1578063059"

message = """📈 <b>Daily Trade Alerts — Mon, June 1 2026</b>

<b>🔥 Urgent Actions:</b>
🟢 <b>PLTR $155.23</b> — STRONG BUY: Q1 revenue +85% YoY, AI demand booming. Target $193–200.
⚠️ <b>NVDA $224.34</b> — DO NOT CHASE: Up 6.2% on COMPUTEX. Wait for dip to $205–215.

<b>📊 Full Portfolio:</b>
🟢 PLTR $155.23 — STRONG BUY: Record earnings, AI platform momentum
🟩 CRM $204.61 — BUY: Anthropic IPO hype, $5B stake, Agentforce growing
🟩 VOO $694.32 — BUY/HOLD: Market above all MAs, broad rally intact
⬜ NVDA $224.34 — HOLD: Strong long-term but near $235 resistance today
⬜ AMZN $259.19 — HOLD: AWS AI capex strong, no urgent action
⬜ GOOGL $370.75 — HOLD: Stable, no catalyst, AI competition persists
⬜ AAPL $305.52 — HOLD: WWDC approaching, watch for AI news
⬜ AMD $499.88 — HOLD: No independent catalyst today

<b>🌍 Market Context:</b>
• Jensen Huang keynoting COMPUTEX — AI hardware sentiment very bullish
• Fed on hold all 2026, possible cuts H1 2027 — tailwind for growth stocks
• New NVDA China export controls today — geopolitical risk for semis

<i>McLean Trade Bot 🤖 | Not financial advice</i>"""

data = urllib.parse.urlencode({"chat_id": chat_id, "text": message, "parse_mode": "HTML"}).encode()
urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=10)
print("✅ Alert sent to Telegram!")
