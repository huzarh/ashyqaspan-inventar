#!/usr/bin/env python3
"""
Builds the static GitHub Pages bundle in ./docs from the live SQLite database (data/envanter.sqlite3)
and static frontend files (./static), including pre-rendered SEO inventory cards.
"""
import html
import json
import re
import shutil
from pathlib import Path
import app

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DOCS_DIR = ROOT / "docs"
UPLOADS_DIR = ROOT / "data" / "uploads"


def build_static_cards(items):
    cards = []
    for item in items[:12]:
        code = f"#{int(item['id']):03d}"
        name = html.escape(item.get("name") or "")
        cat = html.escape(item.get("category") or "")
        model = html.escape(item.get("model") or "—")
        notes = html.escape(item.get("notes") or "")
        photo = html.escape(item.get("photo") or "")
        img_src = f"uploads/{photo}" if photo else "logo.png"
        qty = item.get("quantity", 1)
        qty_str = str(int(qty)) if float(qty).is_integer() else str(qty)
        cards.append(
            f'<article class="card" data-detail="{item["id"]}">'
            f'<div class="card-photo"><img src="{img_src}" alt="{name} — {cat}" width="320" height="240" loading="lazy"></div>'
            f'<div class="card-body">'
            f'<div class="card-top"><span class="code">{code}</span><span class="tag ok">Тексерілді</span></div>'
            f'<h3>{name}</h3>'
            f'<p class="muted">{cat} · {model}</p>'
            f'<p class="notes">{notes}</p>'
            f'</div>'
            f'<div class="card-foot"><div><span class="qty-num">{qty_str}</span> <small>дана</small></div></div>'
            f'</article>'
        )
    return f'<div class="cards">{"".join(cards)}</div>'


def build():
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / ".nojekyll").write_text("", encoding="utf-8")

    store = app.Store(ROOT / "data", seed=False)
    items = store.items()
    movements = store.movements()

    # Pre-render static cards into index.html for SEO crawlers and instant first paint
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        raw_html = index_path.read_text(encoding="utf-8")
        static_cards_html = build_static_cards(items)
        updated_html = re.sub(
            r'(<div id="records" aria-live="polite">).*?(</div>\s*<div class="pagination">)',
            lambda m: m.group(1) + static_cards_html + m.group(2),
            raw_html,
            flags=re.DOTALL,
        )
        index_path.write_text(updated_html, encoding="utf-8")

    for fname in [
        "index.html",
        "style.css",
        "app.js",
        "logo.png",
        "favicon.svg",
        "og-cover.jpg",
        "og-cover.png",
        "robots.txt",
        "sitemap.xml",
        "site.webmanifest",
        "i18n_data.json",
    ]:
        src = STATIC_DIR / fname
        if src.exists():
            shutil.copy2(src, DOCS_DIR / fname)

    payload = {
        "token": "github-pages-static",
        "units": app.UNITS,
        "items": items,
        "movements": movements,
        "app_version": "1.0.0",
    }

    snapshot_str = json.dumps(payload, ensure_ascii=False, indent=2)
    (DOCS_DIR / "data_snapshot.json").write_text(snapshot_str, encoding="utf-8")
    (STATIC_DIR / "data_snapshot.json").write_text(snapshot_str, encoding="utf-8")

    # Pre-generate Excel exports (KZ & RU) sorted 1..N
    (DOCS_DIR / "export_kz.xlsx").write_bytes(app.export_excel(store, lang="kz"))
    (DOCS_DIR / "export_ru.xlsx").write_bytes(app.export_excel(store, lang="ru"))

    # Pre-generate lightweight JSON backup for static hosting
    backup = store.backup()
    backup["images"] = {}
    (DOCS_DIR / "backup.json").write_text(
        json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Copy active uploaded photos to docs/uploads/
    docs_uploads = DOCS_DIR / "uploads"
    docs_uploads.mkdir(parents=True, exist_ok=True)
    active_files = set()
    for item in items:
        fname = (item.get("photo") or "").strip()
        if fname:
            active_files.add(fname)
            src_photo = UPLOADS_DIR / fname
            if src_photo.exists():
                shutil.copy2(src_photo, docs_uploads / fname)

    for existing in docs_uploads.iterdir():
        if existing.is_file() and existing.name not in active_files:
            existing.unlink()

    print(f"Built GitHub Pages bundle in {DOCS_DIR} ({len(items)} items, {len(active_files)} photos).")


if __name__ == "__main__":
    build()
