#!/bin/bash
set -e
echo "=== Installing Playwright Chromium ==="
python -m playwright install --with-deps chromium
echo "=== Chromium installed. Starting scheduler ==="
python scheduler.py
