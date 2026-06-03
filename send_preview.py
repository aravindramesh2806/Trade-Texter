#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Just run the full alert script
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "telegram_alert.py")).read())
