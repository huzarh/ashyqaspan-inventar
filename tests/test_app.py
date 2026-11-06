import base64
import copy
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import uuid
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jwioAAAAASUVORK5CYII='


def body(**changes):
    data = dict(name='ESP32-S3', category='Elektronik', model='N16R8', quantity=2, unit='adet', minimum=1,
                location='Raf A', notes='Başlangıç', request_id=str(uuid.uuid4()))
    data.update(changes)
    return data


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = app.Store(self.temp.name, seed=False)

    def tearDown(self):
        self.temp.cleanup()

    def move(self, item, kind, amount, **changes):
        data = dict(kind=kind, quantity=amount, note='test', version=item['version'], request_id=str(uuid.uuid4()))
        data.update(changes)
        return self.store.move(item['id'], data)

    def test_seed_import_once_and_photo_references(self):
        temp = tempfile.TemporaryDirectory()
        try:
            s = app.Store(temp.name)
            self.assertEqual(len(s.items()), 76)
            self.assertEqual(sum(i['is_package'] for i in s.items()), 8)
            self.assertEqual(sum(i['verified'] for i in s.items()), 0)
            for i in s.items():
                for source in i['sources']:
                    self.assertTrue((app.ROOT/'static/source_photos'/source).is_file(), source)
            self.assertEqual(len(app.Store(temp.name).items()), 76)
        finally:
            temp.cleanup()

    def test_persistent_record_and_photo(self):
        item = self.store.create(body(image=PNG))
        other = app.Store(self.temp.name, seed=False)
        self.assertEqual(other.get(item['id'])['quantity'], 2)
        filename = item['photo']
        self.assertTrue(app.PHOTO_RE.fullmatch(filename))
        self.assertEqual((other.uploads/filename).read_bytes(), base64.b64decode(PNG))

    def test_idempotent_create_and_duplicate(self):
        data = body()
        one, two = self.store.create(data), self.store.create(data)
        self.assertEqual(one['id'], two['id'])
        self.assertEqual(len(self.store.movements()), 1)
        with self.assertRaises(app.Problem) as caught:
            self.store.create(body(name='  esp32-s3  ', model='n16r8'))
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.extra['item_id'], one['id'])
        self.assertEqual(len(self.store.items()), 1)

    def test_negative_nan_precision_and_type_validation(self):
        for q in (-1, 'NaN', float('inf'), '0.0001', True, 'x', 100000001):
            with self.subTest(q=q), self.assertRaises(app.Problem):
                self.store.create(body(quantity=q))
        self.assertEqual(self.store.items(), [])

    def test_stock_flow_and_count(self):
        item = self.store.create(body(quantity=2.125, unit='metre'))
        item = self.move(item, 'giriş', '.375')
        self.assertEqual(item['quantity'], 2.5)
        item = self.move(item, 'çıkış', .5)
        self.assertEqual(item['quantity'], 2)
        item = self.move(item, 'sayım', 0)
        self.assertEqual(item['quantity'], 0)
        self.assertTrue(item['verified'])
        self.assertEqual(len(self.store.movements()), 4)
        self.assertEqual(sum(r['delta'] for r in self.store.movements()), 0)

    def test_insufficient_stock_unchanged(self):
        item = self.store.create(body(quantity=2))
        with self.assertRaises(app.Problem):
            self.move(item, 'çıkış', 3)
        self.assertEqual(self.store.get(item['id'])['quantity'], 2)
        self.assertEqual(len(self.store.movements()), 1)

    def test_movement_replay_exactly_once(self):
        item = self.store.create(body())
        token = str(uuid.uuid4())
        one = self.move(item, 'giriş', 3, request_id=token)
        two = self.move(item, 'giriş', 3, request_id=token)
        self.assertEqual(one['quantity'], 5)
        self.assertEqual(one, two)
        self.assertEqual(len(self.store.movements()), 2)

    def test_concurrent_edits_do_not_lose_updates(self):
        item = self.store.create(body())
        def update(_):
            try:
                self.move(item, 'giriş', 1)
                return 200
            except app.Problem as exc:
                return exc.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(pool.map(update, range(2)))
        self.assertEqual(statuses, [200, 409])
        self.assertEqual(self.store.get(item['id'])['quantity'], 3)

    def test_unit_change_and_stale_metadata_rejected(self):
        item = self.store.create(body())
        with self.assertRaises(app.Problem):
            self.store.edit(item['id'], dict(item, unit='kg'))
        changed = self.store.edit(item['id'], dict(item, location='Dolap 3'))
        self.assertEqual(changed['location'], 'Dolap 3')
        with self.assertRaises(app.Problem) as caught:
            self.store.edit(item['id'], dict(item, location='Dolap 4'))
        self.assertEqual(caught.exception.status, 409)

    def test_archive_preserves_history(self):
        item = self.store.create(body())
        item = self.store.edit(item['id'], dict(item, archived=True))
        self.assertTrue(item['archived'])
        with self.assertRaises(app.Problem):
            self.move(item, 'giriş', 1)
        item = self.store.edit(item['id'], dict(item, archived=False))
        item = self.move(item, 'giriş', 1)
        self.assertEqual(item['quantity'], 3)
        self.assertEqual(len(self.store.movements()), 2)

    def test_bad_photo_rejected(self):
        for image in ('not base64', base64.b64encode(b'<svg onload="x"/>').decode()):
            with self.assertRaises(app.Problem):
                self.store.create(body(image=image))
        self.assertEqual(self.store.items(), [])

    def test_backup_restore_roundtrip_and_automatic_copy(self):
        item = self.store.create(body(image=PNG))
        self.move(item, 'giriş', 4)
        snapshot = self.store.backup()
        saved_items, saved_moves = self.store.items(), self.store.movements()
        self.store.create(body(name='Diğer ürün'))
        result = self.store.restore(snapshot)
        self.assertEqual(result, dict(items=1, movements=2))
        self.assertEqual(self.store.items(), saved_items)
        self.assertEqual(self.store.movements(), saved_moves)
        self.assertTrue(list((self.store.directory/'otomatik_yedekler').glob('*.sqlite3')))

    def test_backup_rejects_corrupt_balance_without_writing(self):
        item = self.store.create(body())
        original = self.store.backup()
        bad = copy.deepcopy(original)
        bad['items'][0]['quantity'] += 1000
        with self.assertRaises(app.Problem):
            self.store.restore(bad)
        self.assertEqual(self.store.backup()['items'], original['items'])
        bad = copy.deepcopy(original)
        bad['items'][0]['sources'] = '["../../secret"]'
        with self.assertRaises(app.Problem):
            self.store.restore(bad)
        self.assertEqual(self.store.get(item['id'])['quantity'], 2)

    def test_backup_rejects_missing_photo(self):
        self.store.create(body(image=PNG))
        bad = self.store.backup(); bad['images'] = {}
        with self.assertRaises(app.Problem):
            self.store.restore(bad)
        self.assertEqual(len(self.store.items()), 1)

    def test_export_valid_ooxml_and_no_formula_injection(self):
        self.store.create(body(name='=SUM(A1:A3)', notes='<html>& "text"'))
        raw = app.export_excel(self.store)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            self.assertIsNone(z.testzip())
            for path in z.namelist():
                ET.fromstring(z.read(path))
            ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            root = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
            self.assertEqual(len(root.findall('.//s:row', ns)), 2)
            self.assertEqual(root.findall('.//s:f', ns), [])
            self.assertIn('=SUM(A1:A3)', ''.join(root.itertext()))
            self.assertIn('xl/worksheets/sheet3.xml', z.namelist())

    def test_unverified_entry_stays_pending_until_count(self):
        # Reproduce a seeded photo-derived entry.
        with self.store.connect() as db:
            ident = self.store._insert(db, body(), str(uuid.uuid4()), seed=True)
        item = self.store.get(ident)
        self.assertFalse(item['verified'])
        item = self.move(item, 'giriş', 2)
        self.assertFalse(item['verified'])
        item = self.move(item, 'sayım', 4)
        self.assertTrue(item['verified'])


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.store = app.Store(cls.temp.name, seed=False)
        cls.server = app.make_server(cls.store, 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:'+str(cls.server.server_address[1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(timeout=3)
        cls.temp.cleanup()

    def request(self, path, payload=None, headers=None, method=None):
        data = json.dumps(payload).encode() if payload is not None else None
        h = {'Content-Type': 'application/json'}
        if headers: h.update(headers)
        request = Request(self.base+path, data=data, headers=h, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, response.read(), response.headers
        except HTTPError as error:
            return error.code, error.read(), error.headers

    def test_frontend_and_local_bootstrap(self):
        status, raw, headers = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn('Malzeme'.encode(), raw)
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        status, raw, _ = self.request('/api/bootstrap')
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(raw)['token'])

    def test_csrf_and_origin_guards(self):
        status, _, _ = self.request('/api/items', body())
        self.assertEqual(status, 403)
        headers = {'X-App-Token': self.server.token, 'Origin': 'https://evil.invalid'}
        status, _, _ = self.request('/api/items', body(), headers)
        self.assertEqual(status, 403)
        status, _, _ = self.request('/api/items', body(name='HTTP kaydı'), {'X-App-Token': self.server.token, 'Origin': self.base})
        self.assertEqual(status, 403)
        status, raw, _ = self.request('/api/items', body(name='HTTP kaydı'), {'X-App-Token': self.server.token, 'Origin': self.base, 'X-Admin-Key': 'ashyqaspan'})
        self.assertEqual(status, 201)
        self.assertNotIn(b'ashyqaspan', self.request('/api/items', body(name='X'), {'X-App-Token': self.server.token, 'X-Admin-Key': 'wrong'})[1])

    def test_cors_for_allowed_origin_only(self):
        self.server.allowed_origins = {'https://huzarh.github.io'}
        self.server.public_hosts = {'pc4.example.ts.net'}
        try:
            ok = {'Origin': 'https://huzarh.github.io'}
            status, _, headers = self.request('/api/items', headers=dict(ok, **{'Access-Control-Request-Method': 'POST'}), method='OPTIONS')
            self.assertEqual(status, 204)
            self.assertEqual(headers['Access-Control-Allow-Origin'], 'https://huzarh.github.io')
            self.assertIn('X-Admin-Key', headers['Access-Control-Allow-Headers'])
            self.assertEqual(self.request('/api/items', headers={'Origin': 'https://evil.invalid'}, method='OPTIONS')[0], 403)
            status, _, headers = self.request('/api/bootstrap', headers=dict(ok, Host='pc4.example.ts.net'))
            self.assertEqual(status, 200)
            self.assertEqual(headers['Access-Control-Allow-Origin'], 'https://huzarh.github.io')
            self.assertIsNone(self.request('/api/bootstrap', headers={'Origin': 'https://evil.invalid'})[2]['Access-Control-Allow-Origin'])
            h = dict(ok, **{'X-App-Token': self.server.token, 'X-Admin-Key': 'ashyqaspan'})
            self.assertEqual(self.request('/api/items', body(name='CORS kaydı'), h)[0], 201)
        finally:
            self.server.allowed_origins = set(); self.server.public_hosts = set()

    @staticmethod
    def fake_go2rtc():
        """Stands in for go2rtc: JPEG frames and a short fMP4 body."""
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.startswith('/api/frame.jpeg?src=cam1'):
                    body, ctype = b'\xff\xd8\xff\xe0fakejpeg', 'image/jpeg'
                elif self.path.startswith('/api/stream.mp4?src=cam1_low'):
                    body, ctype = b'\x00\x00\x00\x18ftypfakemp4', 'video/mp4'
                else:
                    self.send_response(404); self.end_headers(); return
                self.send_response(200); self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)

        srv = HTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    def test_camera_frames_require_admin_key(self):
        fake = self.fake_go2rtc()
        self.server.cameras = app.Cameras('cam1=Шеберхана', 'http://127.0.0.1:%d' % fake.server_address[1])
        key = {'X-Admin-Key': 'ashyqaspan'}
        try:
            self.assertEqual(self.request('/api/cameras')[0], 403)
            self.assertEqual(self.request('/api/cameras/cam1.jpg')[0], 403)
            status, raw, _ = self.request('/api/cameras', headers=key)
            self.assertEqual(status, 200)
            data = json.loads(raw)
            self.assertEqual(data['cameras'][0]['name'], 'Шеберхана')
            self.assertTrue(data['live'])
            status, raw, headers = self.request('/api/cameras/cam1.jpg', headers=key)
            self.assertEqual(status, 200)
            self.assertTrue(raw.startswith(b'\xff\xd8'))
            self.assertEqual(headers['Content-Type'], 'image/jpeg')
            self.assertEqual(self.request('/api/cameras/../app.jpg', headers=key)[0], 404)
            self.assertEqual(self.request('/api/cameras/cam9.jpg', headers=key)[0], 404)
        finally:
            self.server.cameras = app.Cameras(); fake.shutdown(); fake.server_close()

    def test_live_token_and_ptz_require_admin_key(self):
        fake = self.fake_go2rtc()
        self.server.cameras = app.Cameras('cam1=X', 'http://127.0.0.1:%d' % fake.server_address[1])
        try:
            h = {'X-App-Token': self.server.token}
            self.assertEqual(self.request('/api/cameras/live-token', {}, h)[0], 403)
            self.assertEqual(self.request('/api/cameras/cam1/ptz', {'direction': 'left'}, h)[0], 403)
            status, raw, _ = self.request('/api/cameras/live-token', {}, dict(h, **{'X-Admin-Key': 'ashyqaspan'}))
            self.assertEqual(status, 200)
            token = json.loads(raw)['token']
            self.assertEqual(self.request('/api/cameras/cam1/live.mp4?t=bad')[0], 403)
            self.assertEqual(self.request('/api/cameras/cam1/live.mp4?t=1.%s' % token.split('.')[1])[0], 403)
            status, raw, headers = self.request('/api/cameras/cam1/live.mp4?q=low&t=' + token)
            self.assertEqual(status, 200)
            self.assertEqual(headers['Content-Type'], 'video/mp4')
            self.assertIn(b'ftyp', raw)
            self.assertEqual(self.request('/api/cameras/cam7/live.mp4?t=' + token)[0], 404)
            self.assertEqual(self.request('/api/cameras/cam1/ptz', {'direction': 'left'}, dict(h, **{'X-Admin-Key': 'ashyqaspan'}))[0], 404)
        finally:
            self.server.cameras = app.Cameras(); fake.shutdown(); fake.server_close()

    def test_host_and_file_traversal_guards(self):
        self.assertEqual(self.request('/api/bootstrap', headers={'Host': 'evil.invalid'})[0], 403)
        self.assertEqual(self.request('/../app.py')[0], 404)
        self.assertEqual(self.request('/uploads/../../app.py')[0], 404)

    def test_restore_requires_confirmation(self):
        status, _, _ = self.request('/api/restore', self.store.backup(), {'X-App-Token': self.server.token})
        self.assertEqual(status, 400)

    def test_excel_and_backup_downloads(self):
        status, raw, headers = self.request('/api/export.xlsx')
        self.assertEqual(status, 200)
        self.assertTrue(raw.startswith(b'PK'))
        self.assertIn('.xlsx', headers['Content-Disposition'])
        status, raw, headers = self.request('/api/backup.json')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['format'], 'atolye-envanter')
        self.assertIn('attachment', headers['Content-Disposition'])

    def test_admin_key_edit_and_delete(self):
        base_h = {'X-App-Token': self.server.token, 'Origin': self.base}
        auth_h = dict(base_h, **{'X-Admin-Key': 'ashyqaspan'})
        status, raw, _ = self.request('/api/items', body(name='Silinecek Test'), auth_h)
        self.assertEqual(status, 201)
        created = json.loads(raw)
        # Edit without admin key fails with 403
        status, _, _ = self.request('/api/items/%d' % created['id'], dict(created, quantity=9), base_h, method='PATCH')
        self.assertEqual(status, 403)
        # Edit with admin key succeeds and updates quantity
        status, raw, _ = self.request('/api/items/%d' % created['id'], dict(created, quantity=9, unit='takım', allow_unit_change=True), auth_h, method='PATCH')
        self.assertEqual(status, 200)
        updated = json.loads(raw)
        self.assertEqual(updated['quantity'], 9)
        self.assertEqual(updated['unit'], 'takım')
        # Delete without admin key fails with 403
        status, _, _ = self.request('/api/items/%d' % created['id'], None, base_h, method='DELETE')
        self.assertEqual(status, 403)
        # Delete with admin key succeeds
        status, _, _ = self.request('/api/items/%d' % created['id'], None, auth_h, method='DELETE')
        self.assertEqual(status, 200)


if __name__ == '__main__':
    unittest.main()
