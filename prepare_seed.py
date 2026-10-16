"""Önceki Excel'i bir kez başlangıç verisine dönüştürür; çalıştırma için gerekmez."""
import json
import re
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

def extract(path, index):
    with zipfile.ZipFile(path) as z:
        strings = [''.join(t.itertext()) for t in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si', NS)]
        root = ET.fromstring(z.read('xl/worksheets/sheet%d.xml' % index))
        result = []
        for row in root.findall('.//s:sheetData/s:row', NS):
            cells = {}
            for c in row.findall('s:c', NS):
                col = re.sub('[0-9]', '', c.attrib['r'])
                value = c.find('s:v', NS)
                if c.attrib.get('t') == 'inlineStr':
                    val = ''.join(c.find('s:is', NS).itertext())
                elif value is None:
                    val = ''
                elif c.attrib.get('t') == 's':
                    val = strings[int(value.text)]
                else:
                    val = value.text or ''
                cells[col] = val
            result.append(cells)
        return result

def main():
    root = Path(__file__).resolve().parent
    rows = extract(sys.argv[1], 1)
    items = []
    for r in rows:
        if not r.get('A', '').isdigit() or not r.get('C') or not r.get('E', '').isdigit():
            continue
        items.append(dict(name=r['C'], category=r['B'], model=r.get('D', ''), quantity=int(r['E']), unit=r['F'], location='', notes=r.get('J', ''), verification_note=r.get('G', ''), verified=False, is_package=False, sources=[s.strip()+'.jpeg' for s in r.get('I', '').split(',') if s.strip()]))
    for r in extract(sys.argv[1], 2):
        if not r.get('A', '').isdigit() or not r.get('C'):
            continue
        items.append(dict(name=r['C'], category='Kutu / paket kontrolü', model='', quantity=int(r['D']), unit=r['E'], location='Fotoğraftaki raf / masa', notes=r.get('H', ''), verification_note=r['B'], verified=False, is_package=True, sources=[s.strip()+'.jpeg' for s in r.get('F', '').split(',') if s.strip()]))
    (root/'seed.json').write_text(json.dumps({'version': 1, 'items': items}, ensure_ascii=False, indent=2), encoding='utf-8')
    for p in Path(sys.argv[2]).glob('IMG_*.jpeg'):
        shutil.copyfile(str(p), str(root/'static/source_photos'/p.name))
    print('Başlangıç kaydı:', len(items))

if __name__ == '__main__':
    main()
