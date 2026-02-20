#!/bin/bash
echo "Stopping any existing daemon instances to prevent conflicts..."
pkill -f "python src/daemon.py" || true
sleep 1 # Give processes a moment to cleanly terminate

source venv/bin/activate
python src/daemon.py
