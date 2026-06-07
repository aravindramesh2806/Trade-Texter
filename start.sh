#!/bin/bash
cd "$(dirname "$0")"
export TELEGRAM_BOT_TOKEN=$(python3 -c "import json; d=json.load(open('config.json')); print(d.get('TELEGRAM_BOT_TOKEN',''))" 2>/dev/null)
export TELEGRAM_CHAT_ID=$(python3 -c "import json; d=json.load(open('config.json')); print(d.get('TELEGRAM_CHAT_ID',''))" 2>/dev/null)
echo "Starting McLean Trade Bot..."
python3 scheduler.py &
python3 bot_listener.py &
python3 server.py
