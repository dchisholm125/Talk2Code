#!/bin/bash
echo "Stopping any existing daemon instances to prevent conflicts..."
# Match daemon.py regardless of working directory or python path
pkill -f "daemon.py" || true
sleep 1 # Give processes a moment to cleanly terminate

# Free the observability port if something is still holding it (no sudo needed)
OBS_PORT="${OBSERVABILITY_PORT:-8765}"
PIDS=$(lsof -ti:"${OBS_PORT}" 2>/dev/null)
if [ -n "$PIDS" ]; then
    echo "[daemon] Releasing port ${OBS_PORT} held by PID(s): $PIDS"
    echo "$PIDS" | xargs kill -9 2>/dev/null || true
    sleep 1
fi

source venv/bin/activate

# ── Developer DX: enable full verbose logging so NO events are hidden ──────────
export LOG_LEVEL=DEBUG
export VERBOSE_LOGGING=true

echo "[daemon] Starting with LOG_LEVEL=DEBUG, VERBOSE_LOGGING=true"
python src/daemon.py
