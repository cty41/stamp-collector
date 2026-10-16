import sys, unittest, copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_display import fixture
from display import build

class RankingTests(unittest.TestCase):
    def test_interest_before_quantity(self):
        s,c=fixture();gs=build(s,c)['groups'];ids=[g['id'] for g in gs]
        self.assertLess(ids.index('D0'),ids.index('D6'))

    def test_unreviewed_quote_is_not_price(self):
        s,c=fixture();g=next(g for g in build(s,c)['groups'] if g['id']=='D7')
        self.assertIsNone(g['price'])
        self.assertTrue(g['quotes'])

    def test_review_gate_scope_and_stale_evidence(self):
        s,c=fixture();r=s['records'][8];r['revision']=1;r['identity']='已确认'
        e=dict(id='e',observation_id=r['uid'],base_revision=1,review_status='approved',reviewer='test reviewer',reviewed_at='2026-10-16',
               focus=True,kind='special',source='https://example.org/catalog',date='2026-10-16',
               basis='Documented postal use',applicable=True,conditions={'kind':r['kind']})
        s['attention_evidence']=[e]
        self.assertEqual(build(s,c)['groups'][0]['id'],'D6')
        e['review_status']='pending'
        self.assertNotEqual(build(s,c)['groups'][0]['id'],'D6')
        e['review_status']='approved';r['revision']=2
        self.assertNotEqual(build(s,c)['groups'][0]['id'],'D6')

    def test_median_of_comparable_sales_not_highest_quote(self):
        s,c=fixture();r=s['records'][0];r['revision']=1;r['identity']='已确认'
        base=dict(observation_id=r['uid'],base_revision=1,review_status='approved',reviewer='test reviewer',reviewed_at='2026-10-16',focus=False,
          kind='market',source='https://example.org/sale',date='2026-10-16',basis='Same used stamp',
          applicable=True,conditions={'kind':r['kind']},currency='CNY',unit='枚',evidence_type='成交')
        s['attention_evidence']=[dict(base,id=str(i),low=v,high=v) for i,v in enumerate([10,20,90])]
        s['attention_evidence'].append(dict(base,id='listing',low=999,high=999,evidence_type='商家售价'))
        g=next(g for g in build(s,c)['groups'] if g['id']=='D0')
        self.assertEqual(g['price'],20)
        self.assertEqual(g['price_unit'],'枚')
        for field,value in [('currency','USD'),('unit','件'),('review_status','pending')]:
            altered=copy.deepcopy(s)
            for e in altered['attention_evidence']:e[field]=value
            self.assertIsNone(next(g for g in build(altered,c)['groups'] if g['id']=='D0')['price'])
        s['records'][0]['identity']='待确认'
        self.assertIsNone(next(g for g in build(s,c)['groups'] if g['id']=='D0')['price'])

    def test_strip_has_no_count_penalty(self):
        s,c=fixture();s['records'][0]['kind']='连票';s['records'][0]['quantity']=10
        c['groups']=[]
        gs=build(s,c)['groups'];g=next(g for g in gs if g['members']==['O000001'])
        self.assertTrue(g['whole_form'])
        self.assertEqual(g['quantity'],10)

if __name__=='__main__':unittest.main()
