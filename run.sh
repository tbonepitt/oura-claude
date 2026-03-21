#!/bin/bash
# Start the Oura Dashboard locally.
# Your Oura token can be entered in the browser — no .env required.
# Optionally: create a .env file with OURA_TOKEN=... to skip the browser prompt.

if [ -f .env ]; then
  set -a && source .env && set +a
fi

echo "🩺 Oura Dashboard → http://localhost:7891"
echo "   Paste your Oura token in the browser to get started."
open "http://localhost:7891" 2>/dev/null || true
python3 dashboard/server.py
