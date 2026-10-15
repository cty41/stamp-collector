"""Small synthetic fixtures; no access to a user's archive is needed."""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from display import build, signature, rare_key


def fixture():
    records = []
    for n in range(1, 11):
        records.append(dict(id=f'O{n:06}', uid=f'u{n}', active=True,
            name='Example', code='EX-1', face='20', kind='邮票', category='stamp',
            preview=f'assets/{n}.jpg', quantity=1, photo_uid=f'p{n}', identity='待确认',
            region='Example', topic='History', series='EX', level=1, topic_order=0, price=None))
    records[3]['quantity'] = 30
    records[4]['quantity'] = None
    records[5].update(kind='小本票/册页', quantity=12)
    records[6]['face'] = '50'
    records[7].update(code='EX-2', name='Different design')
    records[8].update(level=3, topic='Sports', topic_order=10)
    records[9]['price'] = 2
    members = [[0,1,2], [3], [4], [5], [6], [7], [8], [9]]
    config = {'groups': []}
    for i, indices in enumerate(members):
        rs = [records[j] for j in indices]
        config['groups'].append(dict(id=f'D{i}', members=[r['id'] for r in rs],
            representative=rs[0]['id'], signatures={r['id']:signature(r) for r in rs}))
    return {'records':records, 'items':[], 'item_links':[]}, config


class DisplayTests(unittest.TestCase):
    def setUp(self):
        self.s, self.config = fixture()
        self.d = build(self.s, self.config)

    def group(self, label, d=None):
        return next(g for g in (d or self.d)['groups'] if label in g['members'])

    def test_coverage_determinism_and_no_mutation(self):
        before = copy.deepcopy(self.s)
        self.assertEqual(self.d, build(self.s, self.config))
        self.assertEqual(self.s, before)
        labels = [m for g in self.d['groups'] for m in g['members']]
        self.assertEqual(sorted(labels), sorted(r['id'] for r in self.s['records']))
        self.assertFalse(self.d['warnings'])

    def test_quantity_before_interest_and_unknown_last(self):
        singles = [g for g in self.d['groups'] if g['category']=='single']
        self.assertEqual(singles, sorted(singles, key=rare_key))
        self.assertEqual(self.group('O000001')['quantity'], 3)
        self.assertLess(self.group('O000009')['rank'], self.group('O000001')['rank'])
        self.assertIsNone(singles[-1]['quantity'])

    def test_distinct_faces_designs_and_whole_items(self):
        self.assertNotEqual(self.group('O000001')['id'], self.group('O000007')['id'])
        self.assertNotEqual(self.group('O000001')['id'], self.group('O000008')['id'])
        self.assertEqual(self.group('O000004')['quantity'], 30)
        g = self.group('O000006')
        self.assertEqual((g['category'],g['quantity'],g['unit']), ('whole',1,'件'))

    def test_possible_duplicates_not_deducted(self):
        g = self.group('O000001')
        self.assertEqual(g['quantity'],3)
        self.assertTrue(g['possible_duplicate'])

    def test_explicit_item_links(self):
        self.s['items'] = [{'id':'item', 'data':json.dumps({'quantity':1})}]
        self.s['item_links'] = [{'item_id':'item','observation_id':f'u{i}'} for i in (1,2,3)]
        g = self.group('O000001', build(self.s, self.config))
        self.assertEqual(g['quantity'],1)
        self.assertFalse(g['possible_duplicate'])

    def test_changed_record_not_silently_grouped(self):
        self.s['records'][1]['face'] = '100'
        g = self.group('O000002', build(self.s, self.config))
        self.assertEqual(g['members'], ['O000002'])
        self.assertFalse(g['reviewed'])

    def test_duplicate_assignment_rejected(self):
        entry = copy.deepcopy(self.config['groups'][0]);entry['id']='duplicate'
        self.config['groups'].append(entry)
        with self.assertRaises(ValueError):build(self.s,self.config)

    def test_representative_fixed_and_quotes_not_summed(self):
        self.config['groups'][0]['representative']='O000002'
        self.s['records'][0]['price']=5;self.s['records'][1]['price']=10
        g=self.group('O000001',build(self.s,self.config))
        self.assertEqual(g['preview'],'assets/2.jpg')
        self.assertEqual(g['price'],10)
        self.assertEqual(len(g['quotes']),2)

    def test_no_config_means_separate_records(self):
        d=build(self.s,{'groups':[]})
        self.assertEqual(len(d['groups']),len(self.s['records']))
        self.assertTrue(all(not g['reviewed'] for g in d['groups']))

if __name__=='__main__':unittest.main()
