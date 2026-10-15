import json
import sys
import tempfile
import unittest
from pathlib import Path
from PIL import Image

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
from collection import Store


class PublicSampleTests(unittest.TestCase):
    def test_sample_is_small_and_preview_only(self):
        sample=APP/'sample'
        raw=(sample/'snapshot.js').read_text(encoding='utf8')
        d=json.loads(raw.removeprefix('window.STAMP_DATA=').removesuffix(';'))
        self.assertEqual(set(d),{'sample','records','photos','display'})
        self.assertEqual(d['photos'],[])
        self.assertEqual(len(d['display']['groups']),12)
        self.assertLess(sum(p.stat().st_size for p in sample.rglob('*') if p.is_file()),10*1024*1024)
        forbidden={'notes','location','physical_quantity','high_image','source_crop','history','tasks','aliases'}
        labels=set()
        for r in d['records']:
            self.assertFalse(forbidden & r.keys())
            self.assertTrue((sample/r['preview']).is_file())
            with Image.open(sample/r['preview']) as im:self.assertFalse(im.getexif())
            labels.add(r['id'])
        members=[m for g in d['display']['groups'] for m in g['members']]
        self.assertEqual(len(members),len(labels));self.assertEqual(set(members),labels)
        npc=next(g for g in d['display']['groups'] if 'F13-G01' in g['members'])
        self.assertEqual(npc['quantity'],3)
        for name in ['app.js','style.css','index.html']:
            self.assertEqual((sample/name).read_bytes(),(APP/'offline'/name).read_bytes())

    def test_empty_archive_exports_without_machine_settings(self):
        with tempfile.TemporaryDirectory() as td:
            s=Store(Path(td)/'data');s.init()
            entry=Path(s.export('html',Path(td)/'site'))
            self.assertTrue(entry.is_file())
            self.assertTrue((entry.parent/'snapshot.js').is_file())
            self.assertTrue(s.verify()['ok'])

    def test_excel_without_runtime_has_clear_error(self):
        if (APP/'runtime.json').exists():self.skipTest('Local optional runtime is configured')
        with tempfile.TemporaryDirectory() as td:
            s=Store(Path(td)/'data');s.init()
            with self.assertRaisesRegex(ValueError,'Excel export is optional'):
                s.export('xlsx',Path(td)/'out.xlsx')

if __name__=='__main__':unittest.main()
