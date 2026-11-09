#!/usr/bin/env python3
"""Deletes the oldest camera recordings until the total is under the limit (ATOLYE_REC_LIMIT_GB, default 100).
The newest file of each camera is never touched because ffmpeg may still be writing it."""
import os
from pathlib import Path

ROOT = Path(os.environ.get('ATOLYE_REC_DIR', '/var/lib/atolye-rec'))
LIMIT = float(os.environ.get('ATOLYE_REC_LIMIT_GB', '100')) * 1024**3

files, active = [], set()
for cam in (p for p in ROOT.iterdir() if p.is_dir()) if ROOT.is_dir() else ():
    segs = sorted(cam.glob('*.mp4'))
    if segs:
        active.add(segs[-1])
    files += segs
total = sum(f.stat().st_size for f in files)
deleted = 0
for f in sorted((f for f in files if f not in active), key=lambda p: p.name):
    if total <= LIMIT:
        break
    size = f.stat().st_size
    f.unlink()
    total -= size
    deleted += 1
print('recordings: %.2f GB / %.0f GB, deleted %d file(s)' % (total / 1024**3, LIMIT / 1024**3, deleted))
