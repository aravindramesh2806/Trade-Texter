#!/bin/bash
# McLean Trade Bot — AI feature setup
# Run this once to install dependencies for deep_analysis.py
# Usage: bash setup_ai.sh

echo "Installing TradingAgents + LangGraph..."
pip3 install tradingagents langchain langgraph --break-system-packages -q

echo ""
echo "Done. To use deep analysis, set your API key:"
echo ""
echo "  export ANTHROPIC_API_KEY='sk-ant-...'   # from console.anthropic.com"
echo "  # OR"
echo "  export OPENAI_API_KEY='sk-...'          # from platform.openai.com"
echo ""
echo "Then run:"
echo "  python3 deep_analysis.py NVDA"
echo "  python3 deep_analysis.py AAPL --date 2026-06-03"
