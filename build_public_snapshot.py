#!/usr/bin/env python3
"""Publish a read-only copy of the public data on GitHub Pages, so the site still shows projects, the needs list and
the inventory when the workshop server or its internet link is down.

    python3 build_public_snapshot.py                 # data from the server over SSH (works on the LAN without internet)
    python3 build_public_snapshot.py --source https://pc4-ubuntu.tail8b0712.ts.net

Writes docs/public_snapshot.json (projects, project details with journal entries, needs) and docs/data_snapshot.json
(inventory items, movements, units), copies missing images into docs/uploads, syncs static/{app.js,style.css,index.html}
into docs/ and bumps the ?v= cache-busting number. Commit and push docs/ afterwards.
"""
import argparse
import json
import re
import shutil
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / 'docs'
UPLOADS_REMOTE = '/home/ubuntu/atolye-data/uploads'


def fetcher(source):
    if source == 'ssh':
        def get(path):
            out = subprocess.run(['ssh', 'pc4u', 'curl -s --max-time 30 http://127.0.0.1:8765' + path],
                                 capture_output=True, text=True, check=True).stdout
            return json.loads(out)
    else:
        def get(path):
            with urllib.request.urlopen(source.rstrip('/') + path, timeout=30) as r:
                return json.loads(r.read())
    return get


def bump_version(html):
    def inc(m):
        return '%s?v=%d' % (m.group(1), int(m.group(2)) + 1)
    return re.sub(r'(app\.js|style\.css)\?v=(\d+)', inc, html)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', default='ssh')
    ap.add_argument('--no-bump', action='store_true')
    a = ap.parse_args()
    get = fetcher(a.source)
    boot = get('/api/bootstrap')
    projects = get('/api/projects')
    details = {str(p['id']): get('/api/projects/%d' % p['id']) for p in projects}
    needs = get('/api/needs')
    moves = get('/api/movements')
    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    (DOCS / 'public_snapshot.json').write_text(json.dumps(
        dict(generated_at=stamp, projects=projects, details=details, needs=needs), ensure_ascii=False), encoding='utf-8')
    inventory = dict(token='github-pages-static', units=boot.get('units', []), items=boot['items'], movements=moves,
                     moves=moves, app_version=boot.get('app_version', ''), generated_at=stamp)
    for target in (DOCS / 'data_snapshot.json', ROOT / 'static' / 'data_snapshot.json'):
        target.write_text(json.dumps(inventory, ensure_ascii=False), encoding='utf-8')
    wanted = set()
    for d in details.values():
        if d.get('cover'):
            wanted.add(d['cover'])
        for e in d.get('entries', []):
            wanted.update(e.get('images', []))
    wanted.update(i['photo'] for i in boot['items'] if i.get('photo'))
    wanted.update(n['photo'] for n in needs if n.get('photo'))
    missing = sorted(f for f in wanted if not (DOCS / 'uploads' / f).exists())
    for f in missing:
        subprocess.run(['scp', '-q', 'pc4u:%s/%s' % (UPLOADS_REMOTE, f), str(DOCS / 'uploads' / f)], check=True)
    for f in ('app.js', 'style.css'):
        shutil.copy2(ROOT / 'static' / f, DOCS / f)
    if not a.no_bump:
        for html in (ROOT / 'static' / 'index.html', DOCS / 'index.html'):
            html.write_text(bump_version(html.read_text(encoding='utf-8')), encoding='utf-8')
    print(json.dumps(dict(generated_at=stamp, projects=len(projects), entries=sum(len(d.get('entries', [])) for d in details.values()),
                          needs=len(needs), items=len(boot['items']), movements=len(moves), images_copied=len(missing)), indent=1))


if __name__ == '__main__':
    main()
