#!/bin/bash
# EP Monitor startup script
# Run this once to start the scheduler. It keeps your Mac awake and runs in the background.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Load env vars
export $(cat "$SCRIPT_DIR/.env" | xargs)

# Activate the virtual environment
source "$SCRIPT_DIR/venv311/bin/activate"

# Start scheduler, keep Mac awake, log output to file
caffeinate -i nohup python "$SCRIPT_DIR/scheduler.py" > "$SCRIPT_DIR/scheduler.log" 2>&1 &

echo "EP Monitor started. PID: $!"
echo $! > "$SCRIPT_DIR/scheduler.pid"
echo "To stop it later, run: kill \$(cat $SCRIPT_DIR/scheduler.pid)"
echo "To watch the log: tail -f $SCRIPT_DIR/scheduler.log"
