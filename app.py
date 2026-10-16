#!/usr/bin/env python3
"""Atölye Envanter. Python 3.8+, yalnızca standart kütüphane."""
import argparse
import base64
import binascii
import hashlib
import io
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import unicodedata
import uuid
import webbrowser
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent
UNITS = ('adet', 'takım', 'paket', 'kutu', 'grup', 'metre', 'kg', 'litre')
MAX_IMAGE = 8 * 1024 * 1024
MAX_BACKUP = 120 * 1024 * 1024
PHOTO_RE = re.compile(r'^[a-f0-9]{64}\.(jpg|png|webp)$')
SOURCE_RE = re.compile(r'^IMG_[0-9]{4}\.jpeg$')
ADMIN_KEY = os.environ.get('ATOLYE_ADMIN_KEY', 'ashyqaspan')


class Problem(Exception):
    def __init__(self, message, status=400, **extra):
        self.message, self.status, self.extra = message, status, extra


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def text(value, label, limit=200, required=False):
    if not isinstance(value, str):
        raise Problem('%s metin olmalı.' % label)
    value = value.strip()
    if required and not value:
        raise Problem('%s zorunlu.' % label)
    if len(value) > limit or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise Problem('%s en fazla %s karakter olabilir.' % (label, limit))
    return value


def quantity(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise Problem('Miktar geçerli bir sayı olmalı.')
    try:
        d = Decimal(str(value).replace(',', '.'))
        if not d.is_finite() or d < 0 or d > 100000000 or d * 1000 != (d * 1000).to_integral_value():
            raise Problem('Miktar 0–100.000.000 arasında, en fazla 3 ondalıklı olmalı.')
        return int(d * 1000)
    except InvalidOperation:
        raise Problem('Miktar geçerli bir sayı olmalı.')


def normal(value):
    value = value.translate(str.maketrans('ıİşŞğĞüÜöÖçÇ', 'iissgguuoocc')).casefold()
    return ' '.join(unicodedata.normalize('NFKC', value).split())


def photo_bytes(encoded):
    if not isinstance(encoded, str) or len(encoded) > MAX_IMAGE * 1.4:
        raise Problem('Fotoğraf en fazla 8 MB olabilir.')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise Problem('Fotoğraf verisi okunamadı.')
    if not raw or len(raw) > MAX_IMAGE:
        raise Problem('Fotoğraf boş veya 8 MB sınırını aşıyor.')
    if raw.startswith(b'\xff\xd8\xff'):
        ext = 'jpg'
    elif raw.startswith(b'\x89PNG\r\n\x1a\n'):
        ext = 'png'
    elif raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        ext = 'webp'
    else:
        raise Problem('Fotoğraf JPEG, PNG veya WebP olmalı.')
    return hashlib.sha256(raw).hexdigest()+'.'+ext, raw


SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS items (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL, category TEXT NOT NULL, model TEXT NOT NULL DEFAULT '',
 unit TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity>=0),
 minimum INTEGER NOT NULL DEFAULT 0 CHECK(minimum>=0), location TEXT NOT NULL DEFAULT '',
 notes TEXT NOT NULL DEFAULT '', verification_note TEXT NOT NULL DEFAULT '',
 verified INTEGER NOT NULL DEFAULT 1, is_package INTEGER NOT NULL DEFAULT 0,
 photo TEXT, sources TEXT NOT NULL DEFAULT '[]', archived INTEGER NOT NULL DEFAULT 0,
 version INTEGER NOT NULL DEFAULT 1, duplicate_key TEXT NOT NULL UNIQUE,
 created_token TEXT UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS movements (
 id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER NOT NULL REFERENCES items(id),
 kind TEXT NOT NULL, delta INTEGER NOT NULL, balance INTEGER NOT NULL CHECK(balance>=0),
 note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS movement_item_idx ON movements(item_id,id);
'''


class Store:
    def __init__(self, directory, seed=True):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.uploads = self.directory/'uploads'
        self.uploads.mkdir(exist_ok=True)
        self.db_path = self.directory/'envanter.sqlite3'
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript(SCHEMA)
            db.execute('PRAGMA journal_mode=WAL')
            if not db.execute("SELECT 1 FROM settings WHERE key='initialized'").fetchone():
                if seed:
                    records = json.loads((ROOT/'seed.json').read_text(encoding='utf-8'))['items']
                    for r in records:
                        self._insert(db, r, 'seed-'+str(uuid.uuid4()), seed=True)
                db.execute("INSERT INTO settings VALUES('initialized','1')")
                db.execute("INSERT OR REPLACE INTO settings VALUES('schema_version','1')")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.db_path), timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _fields(self, body):
        unit = body.get('unit', 'adet')
        if unit not in UNITS:
            raise Problem('Geçerli bir birim seçin.')
        name = text(body.get('name', ''), 'Malzeme adı', required=True)
        model = text(body.get('model', ''), 'Marka / model', 300)
        return dict(name=name, category=text(body.get('category', 'Diğer'), 'Kategori', 100, True), model=model,
                    unit=unit, minimum=quantity(body.get('minimum', 0)),
                    location=text(body.get('location', ''), 'Konum', 200),
                    notes=text(body.get('notes', ''), 'Not', 4000),
                    duplicate_key=normal(name)+'|'+normal(model)+'|'+unit)

    def _save_photo(self, encoded):
        filename, raw = photo_bytes(encoded)
        dest = self.uploads/filename
        if not dest.exists():
            temp = dest.with_suffix('.tmp-'+uuid.uuid4().hex)
            temp.write_bytes(raw)
            os.replace(str(temp), str(dest))
        return filename

    def _insert(self, db, body, token, seed=False):
        fields = self._fields(body)
        duplicate = db.execute('SELECT id,name,archived FROM items WHERE duplicate_key=?', (fields['duplicate_key'],)).fetchone()
        if duplicate:
            raise Problem('Bu malzeme zaten kayıtlı. Mevcut kayda stok ekleyebilirsiniz.', 409, item_id=duplicate['id'], duplicate=True)
        q = quantity(body.get('quantity', 0))
        package = body.get('is_package', False)
        if not isinstance(package, bool):
            raise Problem('Kutu seçimi geçersiz.')
        timestamp = now()
        fields.update(quantity=q, verified=int(not seed and not package), is_package=int(package),
                      photo=self._save_photo(body['image']) if body.get('image') else None,
                      sources=json.dumps(body.get('sources', []) if seed else []),
                      verification_note=text(body.get('verification_note', ''), 'Teyit notu', 1000) if seed else '',
                      created_token=token, created_at=timestamp, updated_at=timestamp)
        columns = ','.join(fields)
        marks = ','.join('?' for _ in fields)
        ident = db.execute('INSERT INTO items (%s) VALUES (%s)' % (columns, marks), tuple(fields.values())).lastrowid
        db.execute('INSERT INTO movements(item_id,kind,delta,balance,note,created_at,request_id) VALUES (?,?,?,?,?,?,?)',
                   (ident, 'başlangıç' if seed else 'kayıt', q, q,
                    'Fotoğraftan aktarılan asgari miktar; yerinde sayılmadı.' if seed else 'İlk kayıt', timestamp, token))
        return ident

    @staticmethod
    def serialize(row):
        r = dict(row)
        r['quantity'] = r['quantity']/1000
        r['minimum'] = r['minimum']/1000
        for field in ('verified', 'is_package', 'archived'):
            r[field] = bool(r[field])
        r['sources'] = json.loads(r['sources'])
        r.pop('duplicate_key', None)
        r.pop('created_token', None)
        return r

    def items(self):
        with self.lock, self.connect() as db:
            return [self.serialize(r) for r in db.execute('SELECT * FROM items ORDER BY id DESC')]

    def get(self, ident):
        with self.lock, self.connect() as db:
            row = db.execute('SELECT * FROM items WHERE id=?', (ident,)).fetchone()
            if not row:
                raise Problem('Malzeme bulunamadı.', 404)
            return self.serialize(row)

    def create(self, body):
        token = text(body.get('request_id', ''), 'İşlem kimliği', 100, True)
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT id FROM items WHERE created_token=?', (token,)).fetchone()
            ident = existing['id'] if existing else self._insert(db, body, token)
        return self.get(ident)

    def edit(self, ident, body):
        fields = self._fields(body)
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM items WHERE id=?', (ident,)).fetchone()
            if not row:
                raise Problem('Malzeme bulunamadı.', 404)
            if body.get('version') != row['version']:
                raise Problem('Kayıt başka bir işlemde güncellendi. Listeyi yenileyip tekrar deneyin.', 409)
            if fields['unit'] != row['unit'] and not body.get('allow_unit_change'):
                raise Problem('Stok geçmişini korumak için mevcut kaydın birimi değiştirilemez.')
            duplicate = db.execute('SELECT id FROM items WHERE duplicate_key=? AND id<>?', (fields['duplicate_key'], ident)).fetchone()
            if duplicate:
                raise Problem('Aynı ad, model ve birimle başka bir kayıt var.', 409, item_id=duplicate['id'])
            if body.get('image'):
                fields['photo'] = self._save_photo(body['image'])
            elif body.get('remove_photo') is True:
                fields['photo'] = None
            if 'archived' in body:
                if not isinstance(body['archived'], bool):
                    raise Problem('Arşiv durumu geçersiz.')
                fields['archived'] = int(body['archived'])
            if 'is_package' in body and isinstance(body['is_package'], bool):
                fields['is_package'] = int(body['is_package'])
            timestamp = now()
            if 'quantity' in body and body['quantity'] is not None and str(body['quantity']).strip() != '':
                new_q = quantity(body['quantity'])
                if new_q != row['quantity']:
                    fields['quantity'] = new_q
                    fields['verified'] = 1
                    db.execute('INSERT INTO movements(item_id,kind,delta,balance,note,created_at,request_id) VALUES(?,?,?,?,?,?,?)',
                               (ident, 'sayım', new_q - row['quantity'], new_q, 'Түзету (Карточканы өңдеу)', timestamp, 'edit-'+str(uuid.uuid4())))
            fields['updated_at'] = timestamp
            fields['version'] = row['version']+1
            db.execute('UPDATE items SET '+','.join(k+'=?' for k in fields)+' WHERE id=?', tuple(fields.values())+(ident,))
        return self.get(ident)

    def delete(self, ident):
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT id FROM items WHERE id=?', (ident,)).fetchone()
            if not row:
                raise Problem('Malzeme bulunamadı.', 404)
            db.execute('DELETE FROM movements WHERE item_id=?', (ident,))
            db.execute('DELETE FROM items WHERE id=?', (ident,))
        return {'deleted': ident}

    def move(self, ident, body):
        kind = body.get('kind')
        if kind not in ('giriş', 'çıkış', 'sayım'):
            raise Problem('İşlem türü geçersiz.')
        amount = quantity(body.get('quantity'))
        if kind != 'sayım' and amount == 0:
            raise Problem('Giriş / çıkış miktarı sıfırdan büyük olmalı.')
        token = text(body.get('request_id', ''), 'İşlem kimliği', 100, True)
        note = text(body.get('note', ''), 'İşlem notu', 1000)
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT item_id FROM movements WHERE request_id=?', (token,)).fetchone()
            if prior:
                if prior['item_id'] != ident:
                    raise Problem('İşlem kimliği başka bir kayıtta kullanılmış.', 409)
            else:
                row = db.execute('SELECT * FROM items WHERE id=?', (ident,)).fetchone()
                if not row:
                    raise Problem('Malzeme bulunamadı.', 404)
                if row['archived']:
                    raise Problem('Önce malzemeyi arşivden çıkarın.')
                if body.get('version') != row['version']:
                    raise Problem('Stok değişti. Güncel miktarı kontrol edip tekrar deneyin.', 409)
                balance = amount if kind == 'sayım' else row['quantity'] + (amount if kind == 'giriş' else -amount)
                if balance < 0:
                    raise Problem('Stok yetersiz. Mevcut miktardan fazla çıkış yapılamaz.')
                if balance > 100000000000:
                    raise Problem('Toplam stok sınırı aşıldı.')
                timestamp = now()
                db.execute('INSERT INTO movements(item_id,kind,delta,balance,note,created_at,request_id) VALUES(?,?,?,?,?,?,?)',
                           (ident, kind, balance-row['quantity'], balance, note, timestamp, token))
                db.execute('UPDATE items SET quantity=?,verified=?,updated_at=?,version=version+1 WHERE id=?',
                           (balance, 1 if kind == 'sayım' else row['verified'], timestamp, ident))
        return self.get(ident)

    def movements(self):
        with self.lock, self.connect() as db:
            result = []
            for row in db.execute('SELECT m.*, i.name, i.unit, i.is_package FROM movements m JOIN items i ON i.id=m.item_id ORDER BY m.id DESC'):
                r = dict(row)
                r['delta'] /= 1000
                r['balance'] /= 1000
                result.append(r)
            return result

    def backup(self):
        with self.lock, self.connect() as db:
            items = [dict(r) for r in db.execute('SELECT * FROM items ORDER BY id')]
            movements = [dict(r) for r in db.execute('SELECT * FROM movements ORDER BY id')]
            names = {r['photo'] for r in items if r['photo']}
            images = {n: base64.b64encode((self.uploads/n).read_bytes()).decode('ascii') for n in names}
            return {'format': 'atolye-envanter', 'version': 1, 'created_at': now(), 'items': items, 'movements': movements, 'images': images}

    def restore(self, payload):
        # Validate everything before mutation. Restore runs in a single DB transaction.
        if not isinstance(payload, dict) or payload.get('format') != 'atolye-envanter' or payload.get('version') != 1:
            raise Problem('Bu dosya desteklenen bir Atölye yedeği değil.')
        items, moves, images = payload.get('items'), payload.get('movements'), payload.get('images')
        if not isinstance(items, list) or not isinstance(moves, list) or not isinstance(images, dict) or len(items) > 50000 or len(moves) > 500000:
            raise Problem('Yedek içeriği geçersiz veya çok büyük.')
        decoded = {}
        for name, encoded in images.items():
            valid_name, raw = photo_bytes(encoded)
            if name != valid_name:
                raise Problem('Yedekteki fotoğraf adı / içeriği uyuşmuyor.')
            decoded[name] = raw
        ids, keys, normalized_items = set(), set(), []
        int_fields = ('quantity', 'minimum', 'id', 'version', 'verified', 'is_package', 'archived')
        for r in items:
            if not isinstance(r, dict) or any(type(r.get(k)) is not int for k in int_fields):
                raise Problem('Yedekte geçersiz malzeme alanı var.')
            if r['id'] <= 0 or r['id'] in ids or r['version'] < 1 or r['quantity'] < 0 or r['quantity'] > 100000000000 or r['minimum'] < 0 or r['minimum'] > 100000000000:
                raise Problem('Yedekte malzeme kimliği / miktarı geçersiz.')
            if any(r[k] not in (0, 1) for k in ('verified', 'is_package', 'archived')):
                raise Problem('Yedekte durum alanları geçersiz.')
            clean = self._fields(dict(r, minimum=str(Decimal(r['minimum'])/1000)))
            if clean['duplicate_key'] in keys:
                raise Problem('Yedekte yinelenen malzeme kaydı var.')
            keys.add(clean['duplicate_key']); ids.add(r['id'])
            try:
                sources = json.loads(r.get('sources', '[]'))
            except (TypeError, ValueError):
                raise Problem('Yedekte fotoğraf referansı geçersiz.')
            if not isinstance(sources, list) or any(not isinstance(s, str) or not SOURCE_RE.fullmatch(s) for s in sources):
                raise Problem('Yedekte kaynak fotoğraf yolu geçersiz.')
            ph = r.get('photo')
            if ph is not None and (not isinstance(ph, str) or not PHOTO_RE.fullmatch(ph) or ph not in decoded):
                raise Problem('Yedekte eksik / geçersiz fotoğraf var.')
            clean.update(id=r['id'], quantity=r['quantity'], minimum=r['minimum'], version=r['version'],
                         verified=r['verified'], is_package=r['is_package'], archived=r['archived'], photo=ph,
                         sources=json.dumps(sources), verification_note=text(r.get('verification_note', ''), 'Teyit notu', 1000),
                         created_token=r.get('created_token'), created_at=text(r.get('created_at', ''), 'Tarih', 100, True),
                         updated_at=text(r.get('updated_at', ''), 'Tarih', 100, True))
            if clean['created_token'] is not None:
                clean['created_token'] = text(clean['created_token'], 'İşlem kimliği', 100, True)
            normalized_items.append(clean)
        balance = {i: 0 for i in ids}
        tokens, movement_ids, normalized_moves = set(), set(), []
        for r in sorted(moves, key=lambda m: m.get('id', 0) if isinstance(m, dict) and type(m.get('id')) is int else -1):
            if not isinstance(r, dict) or any(type(r.get(k)) is not int for k in ('id', 'item_id', 'delta', 'balance')):
                raise Problem('Yedekte stok hareketi geçersiz.')
            ident = r['item_id']
            token = text(r.get('request_id', ''), 'İşlem kimliği', 100, True)
            if ident not in ids or r['id'] <= 0 or r['id'] in movement_ids or token in tokens or r.get('kind') not in ('başlangıç', 'kayıt', 'giriş', 'çıkış', 'sayım'):
                raise Problem('Yedekte stok hareketi eşleşmiyor.')
            balance[ident] += r['delta']
            if balance[ident] != r['balance'] or r['balance'] < 0:
                raise Problem('Yedekte stok geçmişi ile bakiye uyuşmuyor.')
            tokens.add(token); movement_ids.add(r['id'])
            normalized_moves.append(dict(id=r['id'], item_id=ident, kind=r['kind'], delta=r['delta'], balance=r['balance'],
                                         note=text(r.get('note', ''), 'İşlem notu', 1000),
                                         created_at=text(r.get('created_at', ''), 'Tarih', 100, True), request_id=token))
        if any(balance[r['id']] != r['quantity'] for r in items):
            raise Problem('Yedekte stok miktarı geçmişle uyuşmuyor.')
        with self.lock:
            # A recoverable copy of the pre-restore database is kept automatically.
            pre = self.directory/'otomatik_yedekler'
            pre.mkdir(exist_ok=True)
            with self.connect() as source:
                target = sqlite3.connect(str(pre/('geri_yukleme_oncesi_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]+'.sqlite3')))
                try:
                    source.backup(target)
                finally:
                    target.close()
            for name, raw in decoded.items():
                dest = self.uploads/name
                if not dest.exists():
                    dest.write_bytes(raw)
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('DELETE FROM movements'); db.execute('DELETE FROM items')
                for table, rows in (('items', normalized_items), ('movements', normalized_moves)):
                    for r in rows:
                        db.execute('INSERT INTO '+table+' ('+','.join(r)+') VALUES ('+','.join('?' for _ in r)+')', tuple(r.values()))
        return {'items': len(items), 'movements': len(moves)}


def xlsx_bytes(sheets):
    """Minimal standards-based OOXML writer; strings never become formulas."""
    def xml_text(value):
        return escape(''.join(c for c in str(value) if c in '\n\t\r' or ord(c) >= 32))
    def letter(n):
        s = ''
        while n:
            n, r = divmod(n-1, 26); s = chr(65+r)+s
        return s
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        declarations = ''.join('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % (i+1) for i in range(len(sheets)))
        z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'+declarations+'</Types>')
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        listing = ''.join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (xml_text(name), i+1, i+1) for i, (name, _) in enumerate(sheets))
        z.writestr('xl/workbook.xml', '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'+listing+'</sheets></workbook>')
        relations = ''.join('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (i+1, i+1) for i in range(len(sheets)))
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+relations+'<Relationship Id="styles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr('xl/styles.xml', '''<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.###"/></numFmts><fonts count="2"><font><sz val="11"/><name val="Arial"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Arial"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF243B53"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFill="1" applyFont="1"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>''')
        for i, (_, rows) in enumerate(sheets):
            parts = []
            for ri, row in enumerate(rows, 1):
                cells = []
                for ci, value in enumerate(row, 1):
                    ref = letter(ci)+str(ri)
                    style = '1' if ri == 1 else ('2' if isinstance(value, (int, float)) else '0')
                    if isinstance(value, (int, float)):
                        cells.append('<c r="%s" s="%s"><v>%s</v></c>' % (ref, style, value))
                    else:
                        cells.append('<c r="%s" s="%s" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, style, xml_text(value if value is not None else '')))
                parts.append('<row r="%d">%s</row>' % (ri, ''.join(cells)))
            widths = ''.join('<col min="%d" max="%d" width="%d" customWidth="1"/>' % (c, c, 12 if c == 1 else 26) for c in range(1, len(rows[0])+1))
            z.writestr('xl/worksheets/sheet%d.xml' % (i+1), '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>'+widths+'</cols><sheetData>'+''.join(parts)+'</sheetData><autoFilter ref="A1:'+letter(len(rows[0]))+str(len(rows))+'"/></worksheet>')
    return buf.getvalue()


def export_excel(store, lang='tr'):
    with store.lock:
        items = sorted(store.items(), key=lambda r: r['id'])
        moves = sorted(store.movements(), key=lambda r: r['id'])
    i18n = {}
    if lang in ('kz', 'ru') and (ROOT/'static/i18n_data.json').is_file():
        i18n = json.loads((ROOT/'static/i18n_data.json').read_text(encoding='utf-8'))
    def tr_item(r):
        if lang not in ('kz', 'ru'):
            return r['name'], r['category'], r['model'], r['unit'], r['location'], r['notes'], r['verification_note']
        entry_obj = i18n.get('items', {}).get(r['name'], {})
        entry = entry_obj.get(lang)
        kz_entry = entry_obj.get('kz')
        name = entry[0] if entry else r['name']
        model = entry[1] if (entry and (not kz_entry or r['model'] == kz_entry[1])) else r['model']
        notes = entry[2] if (entry and (not kz_entry or r['notes'] == kz_entry[2])) else r['notes']
        if lang == 'kz' and kz_entry and r['name'] == kz_entry[0]:
            name, model, notes = r['name'], r['model'], r['notes']
        if r['name'] == 'FPV gözlük' and r['model'] in i18n.get('models', {}):
            m_entry = i18n['models'][r['model']][lang]
            model, notes = m_entry[0], m_entry[1]
        cat = i18n.get('categories', {}).get(r['category'], {}).get(lang, r['category'])
        unit = i18n.get('units', {}).get(r['unit'], {}).get(lang, r['unit'])
        loc = i18n.get('locations', {}).get(r['location'], {}).get(lang, r['location'])
        vnote = i18n.get('verification_notes', {}).get(r['verification_note'], {}).get(lang, r['verification_note'])
        return name, cat, model, unit, loc, notes, vnote
    headers_map = {
        'tr': (
            ['Kayıt no', 'Malzeme', 'Kategori', 'Marka / model', 'Miktar', 'Birim', 'Alt stok sınırı', 'Konum', 'Sayım durumu', 'Kayıt durumu', 'Notlar', 'Teyit notu', 'Kaynak fotoğraflar', 'Fotoğraf dosyası', 'Son güncelleme (UTC)'],
            ['İşlem no', 'Kayıt no', 'Malzeme', 'İşlem', 'Değişim', 'İşlem sonrası stok', 'Birim', 'Açıklama', 'Tarih (UTC)'],
            ('Malzemeler', 'Kutu Kontrolü', 'Stok Hareketleri'),
            ('Teyitli', 'Teyit bekliyor', 'Arşiv', 'Aktif')
        ),
        'kz': (
            ['Код №', 'Заттың аты', 'Категория', 'Маркасы / моделі', 'Саны', 'Өлшем бірлігі', 'Ең аз қалдық', 'Тұрған орны', 'Санақ статусы', 'Статусы', 'Қосымша ақпарат', 'Ескерту', 'Жалпы фотолар', 'Фото файлы', 'Соңғы өзгеріс (UTC)'],
            ['Операция №', 'Код №', 'Заттың аты', 'Түрі', 'Қаншаға өзгерді', 'Қалған саны', 'Өлшем бірлігі', 'Түсініктеме', 'Уақыты (UTC)'],
            ('Қойма заттары', 'Қораптар', 'Кіріс-шығыс тарихы'),
            ('Тексерілді', 'Санау керек', 'Архивте', 'Белсенді')
        ),
        'ru': (
            ['№ записи', 'Материал', 'Категория', 'Марка / модель', 'Количество', 'Ед. изм.', 'Мин. остаток', 'Расположение', 'Статус подсчёта', 'Статус записи', 'Примечания', 'Отметка проверки', 'Исходные фото', 'Файл фото', 'Обновлено (UTC)'],
            ['№ операции', '№ записи', 'Материал', 'Операция', 'Изменение', 'Остаток после', 'Ед. изм.', 'Примечание', 'Дата (UTC)'],
            ('Материалы', 'Проверка коробок', 'Движение склада'),
            ('Подтверждено', 'Ожидает подсчёта', 'Архив', 'Активно')
        )
    }
    item_hdr, hist_hdr, sheet_names, st_labels = headers_map.get(lang, headers_map['tr'])
    def rows(package):
        result = [item_hdr]
        for r in items:
            if r['is_package'] != package:
                continue
            name, cat, model, unit, loc, notes, vnote = tr_item(r)
            result.append([r['id'], name, cat, model, r['quantity'], unit, r['minimum'], loc,
                           st_labels[0] if r['verified'] else st_labels[1], st_labels[2] if r['archived'] else st_labels[3], notes, vnote, ', '.join(r['sources']), r['photo'] or '', r['updated_at']])
        return result
    history = [hist_hdr]
    for r in moves:
        if lang in ('kz', 'ru'):
            entry = i18n.get('items', {}).get(r['name'], {}).get(lang)
            m_name = entry[0] if entry else r['name']
            m_kind = i18n.get('kinds', {}).get(r['kind'], {}).get(lang, r['kind'])
            m_unit = i18n.get('units', {}).get(r['unit'], {}).get(lang, r['unit'])
            m_note = i18n.get('move_notes', {}).get(r['note'], {}).get(lang, r['note'])
        else:
            m_name, m_kind, m_unit, m_note = r['name'], r['kind'], r['unit'], r['note']
        history.append([r['id'], r['item_id'], m_name, m_kind, r['delta'], r['balance'], m_unit, m_note, r['created_at']])
    return xlsx_bytes([(sheet_names[0], rows(False)), (sheet_names[1], rows(True)), (sheet_names[2], history)])


class Handler(BaseHTTPRequestHandler):
    server_version = 'Atolye/1.0'

    def log_message(self, fmt, *args):
        logging.info('%s %s', self.command, self.path.split('?')[0])

    def reply(self, status, body, content_type='application/json; charset=utf-8', filename=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        if filename:
            self.send_header('Content-Disposition', 'attachment; filename="'+filename+'"')
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        host = self.headers.get('Host', '')
        port = str(self.server.server_address[1])
        lan_re = r'^(127\.0\.0\.1|localhost|192\.168\.[0-9]{1,3}\.[0-9]{1,3}|10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[0-1])\.[0-9]{1,3}\.[0-9]{1,3}):' + re.escape(port) + r'$'
        if host not in self.server.allowed_hosts and not re.match(lan_re, host):
            raise Problem('Bu sunucu yalnızca yerel ağda kullanılabilir.', 403)

    def do_GET(self):
        try:
            self.valid_host()
            parsed = urlparse(self.path)
            path = parsed.path
            if path == '/api/bootstrap':
                return self.reply(200, {'token': self.server.token, 'units': UNITS, 'items': self.server.store.items(), 'app_version': '1.0.0'})
            if path == '/api/items':
                return self.reply(200, self.server.store.items())
            if path == '/api/movements':
                return self.reply(200, self.server.store.movements())
            if path == '/api/export.xlsx':
                lang = parse_qs(parsed.query).get('lang', ['tr'])[0]
                prefix = {'kz': 'Sheberhana_Tizimdemesi_', 'ru': 'Masterskaya_Inventar_'}.get(lang, 'Atolye_Envanteri_')
                return self.reply(200, export_excel(self.server.store, lang), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', prefix+datetime.now().strftime('%Y-%m-%d')+'.xlsx')
            if path == '/api/backup.json':
                payload = json.dumps(self.server.store.backup(), ensure_ascii=False).encode('utf-8')
                if len(payload) > MAX_BACKUP:
                    raise Problem('Tam yedek 120 MB sınırını aşıyor. Uygulamayı kapatıp proje ve data klasörünün tamamını kopyalayın.', 413)
                return self.reply(200, payload, filename='Atolye_Tam_Yedek_'+datetime.now().strftime('%Y-%m-%d')+'.json')
            if path.startswith('/uploads/'):
                name = path[len('/uploads/'):]
                if not PHOTO_RE.fullmatch(name):
                    raise Problem('Fotoğraf bulunamadı.', 404)
                file = self.server.store.uploads/name
            else:
                relative = 'index.html' if path == '/' else path[1:]
                file = (ROOT/'static'/relative).resolve()
                if (ROOT/'static').resolve() not in file.parents:
                    raise Problem('Dosya bulunamadı.', 404)
            if not file.is_file():
                raise Problem('Dosya bulunamadı.', 404)
            types = {'.html': 'text/html; charset=utf-8', '.js': 'application/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.jpeg': 'image/jpeg', '.jpg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp', '.svg': 'image/svg+xml'}
            return self.reply(200, file.read_bytes(), types.get(file.suffix, 'application/octet-stream'))
        except Problem as exc:
            self.reply(exc.status, dict(error=exc.message, **exc.extra))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            logging.exception('GET işlemi başarısız')
            self.reply(500, {'error': 'İşlem tamamlanamadı. Terminal kaydını kontrol edin.'})

    def do_POST(self):
        self.mutate()

    def do_PATCH(self):
        self.mutate()

    def do_DELETE(self):
        self.mutate()

    def check_admin(self, body=None):
        provided = (self.headers.get('X-Admin-Key') or (body or {}).get('admin_key') or '').strip()
        if not provided or not secrets.compare_digest(provided.lower(), ADMIN_KEY.lower()):
            raise Problem('Рұқсат кілті қате! / Неверный ключ доступа! (ashyqaspan)', 403, auth_required=True)

    def mutate(self):
        try:
            self.valid_host()
            origin = self.headers.get('Origin')
            if origin and origin != 'http://'+self.headers.get('Host', ''):
                raise Problem('Geçersiz istek kaynağı.', 403)
            if not secrets.compare_digest(self.headers.get('X-App-Token', ''), self.server.token):
                raise Problem('Oturumu yenileyip tekrar deneyin.', 403)
            path = urlparse(self.path).path
            if self.command == 'DELETE':
                match_del = re.fullmatch('/api/items/([0-9]+)', path)
                if not match_del:
                    raise Problem('İşlem bulunamadı.', 404)
                self.check_admin()
                return self.reply(200, self.server.store.delete(int(match_del.group(1))))
            maximum = MAX_BACKUP if path == '/api/restore' else 12 * 1024 * 1024
            try:
                size = int(self.headers.get('Content-Length', '-1'))
            except ValueError:
                raise Problem('Geçersiz istek boyutu.')
            if size < 0 or size > maximum:
                raise Problem('Dosya / istek boyutu sınırı aşıldı.', 413)
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise Problem('JSON veri bekleniyor.', 415)
            try:
                body = json.loads(self.rfile.read(size).decode('utf-8'))
            except (ValueError, UnicodeError):
                raise Problem('Gönderilen veri okunamadı.')
            if not isinstance(body, dict):
                raise Problem('Geçerli bir kayıt gönderin.')
            if path == '/api/auth' and self.command == 'POST':
                self.check_admin(body)
                return self.reply(200, {'ok': True})
            store = self.server.store
            if path == '/api/items' and self.command == 'POST':
                return self.reply(201, store.create(body))
            match = re.fullmatch('/api/items/([0-9]+)(/movements)?', path)
            if match:
                ident = int(match.group(1))
                self.check_admin(body)
                if match.group(2) and self.command == 'POST':
                    return self.reply(200, store.move(ident, body))
                if not match.group(2) and self.command == 'PATCH':
                    return self.reply(200, store.edit(ident, body))
            if path == '/api/restore' and self.command == 'POST':
                if self.headers.get('X-Confirm-Restore') != 'replace':
                    raise Problem('Geri yükleme onayı eksik.')
                self.check_admin(body)
                return self.reply(200, store.restore(body))
            raise Problem('İşlem bulunamadı.', 404)
        except Problem as exc:
            self.reply(exc.status, dict(error=exc.message, **exc.extra))
        except sqlite3.IntegrityError:
            self.reply(400, {'error': 'Veri tutarsızlığı nedeniyle işlem kaydedilmedi.'})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            logging.exception('Kayıt işlemi başarısız')
            self.reply(500, {'error': 'İşlem kaydedilemedi. Terminal kaydını kontrol edin.'})


def make_server(store, port, host='0.0.0.0'):
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.store = store
    server.token = secrets.token_urlsafe(32)
    actual_port = server.server_address[1]
    server.allowed_hosts = {'127.0.0.1:'+str(actual_port), 'localhost:'+str(actual_port)}
    return server


def main():
    parser = argparse.ArgumentParser(description='Atölye malzeme kayıt sistemi')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--data-dir', type=Path, default=ROOT/'data')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    try:
        store = Store(args.data_dir)
        server = make_server(store, args.port, args.host)
    except (OSError, sqlite3.Error) as exc:
        print('Başlatılamadı:', exc)
        print('Port doluysa: python3 app.py --port 8766')
        return 1
    url = 'http://127.0.0.1:%s' % server.server_address[1]
    print('\nATÖLYE ENVANTER\nBilgisayar: %s\nTelefon (Aynı Wi-Fi): http://192.168.8.169:%s\nVeriler: %s\nKapatmak için Ctrl+C.\n' % (url, server.server_address[1], store.directory.resolve()), flush=True)
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nKapatıldı. Kayıtlar diskte saklanıyor.')
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
