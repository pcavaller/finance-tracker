#!/bin/bash
# Kill any existing bot instance before starting
pkill -f 'python.*bot\.py' 2>/dev/null || true
sleep 1

# Wait for network connectivity
until curl -sf --max-time 5 https://api.telegram.org > /dev/null 2>&1; do
    echo "$(date): Waiting for network..."
    sleep 5
done
echo "$(date): Network ready, starting bot."
exec /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 /Users/pablocavallergrau/finance-bot/bot.py
