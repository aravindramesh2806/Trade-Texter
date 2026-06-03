#!/usr/bin/env python3
"""
Live Stocks Dashboard Server
Run: python3 server.py
Then open: http://localhost:8080
"""
import json, os, sys, urllib.request, urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Install yfinance if needed
try:
    import yfinance as yf
except ImportError:
    print("Installing yfinance...")
    os.system(f"{sys.executable} -m pip install yfinance -q")
    import yfinance as yf

DIR = os.path.dirname(os.path.abspath(__file__))

# ── Telegram config ──────────────────────────────────────────────
TELEGRAM_TOKEN   = "YOUR_BOT_TOKEN_HERE"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID_HERE"

def send_telegram(message: str, parse_mode: str = "HTML") -> bool:
    """Send a Telegram message. Returns True on success."""
    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": parse_mode,
        }).encode()
        req  = urllib.request.Request(url, data=data, method="POST")
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"  Telegram error: {e}")
        return False
# ─────────────────────────────────────────────────────────────────


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/quotes':
            self.handle_quotes(parsed)
        elif parsed.path == '/api/notify-test':
            self.handle_notify_test()
        elif parsed.path == '/api/alert':
            self.handle_alert(parsed)
        else:
            super().do_GET()

    def handle_quotes(self, parsed):
        params  = parse_qs(parsed.query)
        symbols = params.get('symbols', [''])[0].split(',')
        symbols = [s.strip().upper() for s in symbols if s.strip()]
        try:
            data    = []
            tickers = yf.Tickers(' '.join(symbols))
            for sym in symbols:
                try:
                    t    = tickers.tickers[sym]
                    fi   = t.fast_info
                    prev = fi.previous_close or fi.regular_market_previous_close or 0
                    price = fi.last_price or 0
                    change = price - prev
                    changePct = (change / prev * 100) if prev else 0
                    data.append({
                        'symbol':                    sym,
                        'regularMarketPrice':        round(price, 2),
                        'regularMarketChange':       round(change, 2),
                        'regularMarketChangePercent':round(changePct, 2),
                        'regularMarketOpen':         round(fi.open or 0, 2),
                        'regularMarketDayHigh':      round(fi.day_high or 0, 2),
                        'regularMarketDayLow':       round(fi.day_low or 0, 2),
                        'regularMarketPreviousClose':round(prev, 2),
                        'shortName': sym,
                    })
                except Exception as e:
                    data.append({'symbol': sym, 'error': str(e)})
            self._json(200, {'result': data})
        except Exception as e:
            self._json(500, {'error': str(e)})

    def handle_notify_test(self):
        ok = send_telegram(
            "🤖 <b>Mclean Trade Bot connected!</b>\n\n"
            "✅ Your daily trade alerts are set up.\n"
            "📈 You'll receive buy/sell signals every weekday at <b>7:00 AM</b>.\n\n"
            "<i>Portfolio: AAPL · GOOGL · PLTR · VOO · NVDA · AMD · AMZN · CRM</i>"
        )
        self._json(200, {'ok': ok, 'message': 'Test notification sent!' if ok else 'Failed'})

    def handle_alert(self, parsed):
        """Send a custom alert. ?msg=your+message"""
        params  = parse_qs(parsed.query)
        msg     = params.get('msg', ['Alert from your trading dashboard'])[0]
        ok      = send_telegram(f"📊 <b>Trade Alert</b>\n\n{msg}")
        self._json(200, {'ok': ok})

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        if '/api/' in args[0]:
            print(f"  {args[0]} → {args[1]}")


if __name__ == '__main__':
    port = 8080
    print(f"\n✅ Stock dashboard running at http://localhost:{port}")
    print(f"   Telegram bot: @Mclean_trade_bot → Chat {TELEGRAM_CHAT_ID}")
    print("   Press Ctrl+C to stop\n")
    os.system(f"open http://localhost:{port}/live-stocks.html")
    HTTPServer(('', port), Handler).serve_forever()
