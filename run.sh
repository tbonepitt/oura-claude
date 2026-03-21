#!/bin/bash
# Load .env and start the dashboard
set -a && source .env && set +a
echo "🩺 Starting Oura Dashboard on http://localhost:7891"
python3 dashboard/server.py
