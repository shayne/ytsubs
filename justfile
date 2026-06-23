#!/usr/bin/env -S just --justfile

init:
    python3 -m venv .venv
    source .venv/bin/activate
    pip3 install -r requirements.txt

firstrun:
    python3 scrape_channel_stats.py
    python3 scrape_videos.py

daily:
    python3 scrape_videos.py

refreshfull:
    python3 scrape_channel_stats.py
    python3 scrape_videos.py

generate:
    python3 generate_feed.py
