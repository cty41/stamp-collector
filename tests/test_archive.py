import io,json,sys,tempfile,unittest,threading,zipfile,sqlite3,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from collection import Store,Conflict,uid,image_bytes,dump
from PIL import Image
from server import make_server
from urllib.request import Request,urlopen
from urllib.error import HTTPError

class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.s=Store(self.root/'archive');self.s.init()
        self.img=image_bytes(Image.new('RGB',(120,80),'#ec7a65'))
        self.photo=self.s.import_photo(data=self.img,name='a.png')
        self.task=self.s.extraction(self.photo['id'],[[5,5,60,65]])
        self.label=self.s.accept_extraction(self.task['task'],[],uid())['observations'][0]
    def tearDown(self):self.tmp.cleanup()
    def row(self):return next(r for r in self.s.snapshot()['records'] if r['id']==self.label)
    def result(self,changes=None):
        r=self.row();return {'observation_id':r['uid'],'input_revision':r['revision'],'changes':changes or {'name':'昭君出塞','identity':'较可能'},'provenance':{'basis':'票面文字与目录对照','method':'文字检索','sources':['https://example.com/catalog']}}
    def test_import_rename_and_init_are_idempotent(self):
        for _ in range(2):self.assertTrue(self.s.import_photo(data=self.img,name='renamed.png')['duplicate']);self.s.init()
        self.assertEqual(len(self.s.snapshot()['photos']),1);self.assertEqual(len(self.s.snapshot()['records']),1)
    def test_recompressed_image_not_automerged(self):
        b=image_bytes(Image.new('RGB',(120,80),'#ec7a65'),'JPEG');out=self.s.import_photo(data=b,name='a.jpg')
        self.assertFalse(out['duplicate']);self.assertTrue(out['possible_duplicates']);self.assertEqual(len(self.s.snapshot()['photos']),2)
    def test_task_cache_force_and_official_unchanged(self):
        before=self.row();a=self.s.request_analysis(self.label,result=self.result());b=self.s.request_analysis(self.label,result=self.result());c=self.s.request_analysis(self.label,result=self.result(),rerun=True)
        self.assertTrue(b['cached']);self.assertEqual(a['task'],b['task']);self.assertNotEqual(a['task'],c['task']);self.assertEqual(self.row(),before)
    def test_accept_partial_repeat_and_reject(self):
        cid=self.s.request_analysis(self.label,result=self.result())['result']['candidate'];op=uid()
        a=self.s.review(cid,{'name':'accept'},op);b=self.s.review(cid,{'name':'accept'},op);self.assertEqual(a,b)
        self.s.review(cid,{'identity':'reject'},uid());r=self.row();self.assertEqual(r['name'],'昭君出塞');self.assertEqual(r['identity'],'待确认')
        self.assertEqual(len(self.s.snapshot()['history']),2)
    def test_stale_candidate_cannot_override_personal_edit(self):
        cid=self.s.request_analysis(self.label,result=self.result())['result']['candidate'];r=self.row();self.s.edit(r['uid'],{'notes':'我的备注'},r['revision'],uid())
        with self.assertRaises(Conflict):self.s.review(cid,{'name':'accept'},uid())
        self.assertEqual(self.row()['notes'],'我的备注');self.assertEqual(self.row()['name'],'待辨认邮票')
    def test_model_cannot_edit_personal_fields_or_fake_upload(self):
        with self.assertRaises(ValueError):self.s.request_analysis(self.label,result=self.result({'notes':'模型覆盖'}))
        r=self.result();r['provenance']['method']='以图搜图'
        with self.assertRaises(ValueError):self.s.request_analysis(self.label,result=r)
    def test_stale_import_and_operation_conflict(self):
        result=self.result();r=self.row();op=uid();self.s.edit(r['uid'],{'location':'A册'},1,op)
        with self.assertRaises(Conflict):self.s.request_analysis(self.label,result=result)
        with self.assertRaises(Conflict):self.s.edit(r['uid'],{'location':'B册'},1,op)
    def test_rotation_from_original_and_undo(self):
        original=self.row();self.s.edit(original['uid'],{'orientation':{'angle_deg':90,'reason':'用户','review_status':'已复核'}},1,uid());rot=self.row()
        self.assertEqual(rot['orientation']['image_size'],[60,55]);self.s.edit(rot['uid'],{'orientation':{'angle_deg':0}},2,uid());self.assertEqual(self.row()['high_image'],original['high_image'])
        h=self.s.snapshot()['history'][-1];self.s.undo(h['id'],3,uid());self.assertEqual(self.row()['high_image'],rot['high_image']);self.assertEqual(len(self.s.snapshot()['history']),4)
    def test_split_preserves_parent_and_lineage(self):
        t=self.s.extraction(self.photo['id'],[[5,5,30,65],[30,5,60,65]]);r=self.row();res=self.s.accept_extraction(t['task'],[{'id':r['uid'],'revision':1}],uid());self.assertEqual(len(res['observations']),2)
        snap=self.s.snapshot();self.assertEqual(len(snap['records']),3);self.assertFalse(self.row()['active']);self.assertEqual(len(snap['lineage']),2)
        again=self.s.accept_extraction(t['task'],[],uid());self.assertEqual(again['observations'],res['observations']);self.assertEqual(len(self.s.snapshot()['records']),3)
    def test_same_boxes_reuse_and_new_sort_keeps_ids(self):
        t=self.s.extraction(self.photo['id'],[[5,5,60,65]]);self.assertTrue(t['cached']);self.s.accept_extraction(t['task'],[],uid())
        self.s.edit(self.label,{'name':'另一名称','topic':'著名人物'},1,uid());self.assertEqual(self.row()['id'],self.label);self.assertEqual(len(self.s.snapshot()['records']),1)
    def test_invalid_crop_transaction_leaves_no_record(self):
        before=len(self.s.snapshot()['records'])
        with self.assertRaises(ValueError):self.s.extraction(self.photo['id'],[[0,0,900,800]])
        self.assertEqual(len(self.s.snapshot()['records']),before);self.assertTrue(self.s.verify()['ok'])
    def test_backup_restore_and_exports_do_not_mutate(self):
        self.s.edit(self.label,{'notes':'保留备注'},1,uid());before=self.s.db.read_bytes();self.s.export('json',self.root/'all.json');self.s.export('html',self.root/'html')
        self.assertEqual(before,self.s.db.read_bytes());self.assertTrue((self.root/'html/snapshot.js').exists())
        self.s.backup(self.root/'backup.zip');report=Store.restore(self.root/'backup.zip',self.root/'restored');self.assertTrue(report['ok'])
        restored=Store(self.root/'restored');self.assertEqual(restored.snapshot()['history'],self.s.snapshot()['history'])
        self.assertEqual(restored.snapshot()['records'],self.s.snapshot()['records'])
        with self.assertRaises(ValueError):Store.restore(self.root/'backup.zip',self.root/'restored')
    def test_explicit_links_do_not_guess_stock(self):
        self.assertEqual(self.s.snapshot()['items'],[]);self.s.link('item',[self.label],{'name':'同一枚','quantity':1},uid());self.s.link('variety',[self.label],{'name':'待定品种'},uid())
        s=self.s.snapshot();self.assertEqual(len(s['items']),1);self.assertEqual(len(s['varieties']),1);self.assertEqual(self.row()['quantity'],1)
    def test_price_range_does_not_replace_image(self):
        before=self.row()['high_image'];r=self.row();self.s.edit(self.label,{'low':1,'high':2,'price':1.5},r['revision'],uid())
        self.assertEqual(self.row()['high_image'],before);self.assertEqual(self.row()['high'],2);self.assertTrue(self.s.verify()['ok'])
    def test_later_edit_supersedes_accepted_candidate(self):
        cid=self.s.request_analysis(self.label,result=self.result({'name':'候选名称'}))['result']['candidate']
        self.s.review(cid,{'name':'accept'},uid());self.s.edit(self.label,{'name':'修订名称'},2,uid())
        c=next(c for c in self.s.snapshot()['candidates'] if c['id']==cid);self.assertEqual(c['status'],'已被后续修订替代')
    def test_phone_exif_coordinates_follow_display_orientation(self):
        im=Image.new('RGB',(80,120),'blue');exif=Image.Exif();exif[274]=6;buf=io.BytesIO();im.save(buf,'JPEG',exif=exif)
        p=self.s.import_photo(data=buf.getvalue(),name='phone.jpg');photo=next(p2 for p2 in self.s.snapshot()['photos'] if p2['id']==p['id']);self.assertEqual((photo['width'],photo['height']),(120,80))
        task=self.s.extraction(p['id'],[[90,5,115,30]]);self.s.accept_extraction(task['task'],[],uid());self.assertTrue(self.s.verify()['ok'])
    def test_server_security_and_edit(self):
        server=make_server(self.s,0);t=threading.Thread(target=server.serve_forever,daemon=True);t.start();base=f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(base+'/api/snapshot') as r:state=json.load(r)
            body=dump({'id':self.label,'revision':1,'changes':{'notes':'HTTP测试'},'operation':uid()}).encode()
            with self.assertRaises(HTTPError):urlopen(Request(base+'/api/edit',body,{'Content-Type':'application/json'}))
            with self.assertRaises(HTTPError):urlopen(Request(base+'/api/edit',body,{'Content-Type':'application/json','Origin':'http://evil.example','X-Archive-Token':state['session_token']}))
            with urlopen(Request(base+'/api/edit',body,{'Content-Type':'application/json','Origin':base,'X-Archive-Token':state['session_token']})) as r:self.assertTrue(json.load(r)['saved'])
            self.assertEqual(self.row()['notes'],'HTTP测试')
            with self.assertRaises(HTTPError):urlopen(base+'/assets/../../outside.txt')
        finally:server.shutdown();server.server_close();t.join()
if __name__=='__main__':unittest.main(verbosity=2)
