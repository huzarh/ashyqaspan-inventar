#!/usr/bin/env python3
"""
Builds the static GitHub Pages bundle in ./docs from the live SQLite database (data/envanter.sqlite3)
and static frontend files (./static).
"""
import json
import shutil
from pathlib import Path
import app

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DOCS_DIR = ROOT / "docs"
UPLOADS_DIR = ROOT / "data" / "uploads"


def build():
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / ".nojekyll").write_text("", encoding="utf-8")

    for fname in ["index.html", "style.css", "app.js", "logo.png", "favicon.svg", "i18n_data.json"]:
        src = STATIC_DIR / fname
        if src.exists():
            shutil.copy2(src, DOCS_DIR / fname)

    store = app.Store(ROOT / "data", seed=False)
    items = store.items()
    movements = store.movements()

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
