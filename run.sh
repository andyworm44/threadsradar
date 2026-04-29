#!/bin/bash
cd "$(dirname "$0")"
/usr/sbin/lsof -ti:8765 | xargs kill -9 2>/dev/null
sleep 1
python3 scraper.py
