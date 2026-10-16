"""Local, versioned philatelic archive. Only review/edit promote official data."""
from __future__ import annotations
import argparse, base64, collections, contextlib, hashlib, io, json, math, os, re, shutil
import sqlite3, subprocess, sys, tempfile, threading, uuid, zipfile
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image, ImageOps

APP = Path(__file__).resolve().parent
VERSION = '1.0.0'
LOCK = threading.RLock()
PERSONAL = {'notes', 'location', 'physical_quantity'}
IDENTITY = {'name','code','identity','basis','research_source','method','unresolved','face','region','topic','tags','series','series_status','kind','condition','related','duplicate'}
PRICE = {'price','low','high','price_unit','price_source','quote_date','access_date','valuation','confidence','evidence'}
MODEL_FIELDS = IDENTITY | PRICE | {'orientation'}
def now(): return datetime.now(timezone.utc).isoformat()
def dump(v): return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(',',':'), allow_nan=False)
def digest(b): return hashlib.sha256(b).hexdigest()
def uid(): return str(uuid.uuid4())
def atomic(path, data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.pending-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if Path(tmp).exists(): Path(tmp).unlink()
def jsonfile(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def image_bytes(im, format='PNG'):
    f=io.BytesIO(); im.save(f,format,**({'quality':90,'optimize':True} if format=='JPEG' else {})); return f.getvalue()
def orient(im,angle):
    angle=float(angle)
    if not math.isfinite(angle): raise ValueError('旋转角度必须是有限数值')
    angle%=360
    if angle==0:return im.copy()
    if angle in (90,180,270):return im.transpose({90:Image.Transpose.ROTATE_90,180:Image.Transpose.ROTATE_180,270:Image.Transpose.ROTATE_270}[angle])
    return im.convert('RGBA').rotate(angle,Image.Resampling.BICUBIC,expand=True,fillcolor=(255,255,255,0))
def phash(im):
    im=ImageOps.exif_transpose(im).convert('L').resize((9,8));p=list(im.getdata())
    return f'{sum((p[y*9+x]>p[y*9+x+1])<<(y*8+x) for y in range(8) for x in range(8)):016x}'

class Conflict(ValueError): pass
class ClosingConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()
class Store:
    def __init__(self,root):
        self.root=Path(root).resolve();self.db=self.root/'collection.sqlite3'
    def init(self):
        self.root.mkdir(parents=True,exist_ok=True)
        with self.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS assets(path TEXT PRIMARY KEY,sha TEXT NOT NULL,size INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS photos(id TEXT PRIMARY KEY,label TEXT UNIQUE NOT NULL,sha TEXT UNIQUE NOT NULL,path TEXT NOT NULL,aliases TEXT NOT NULL,width INTEGER,height INTEGER,batch TEXT,phash TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY,label TEXT UNIQUE NOT NULL,photo_id TEXT REFERENCES photos(id),revision INTEGER NOT NULL,data TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,created TEXT);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,task_key TEXT NOT NULL,attempt INTEGER NOT NULL,status TEXT NOT NULL,request TEXT NOT NULL,result TEXT,error TEXT,created TEXT,updated TEXT,UNIQUE(task_key,attempt));
            CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY,task_id TEXT REFERENCES tasks(id),observation_id TEXT REFERENCES observations(id),base_revision INTEGER,data TEXT NOT NULL,decisions TEXT NOT NULL,status TEXT NOT NULL,created TEXT);
            CREATE TABLE IF NOT EXISTS history(id TEXT PRIMARY KEY,observation_id TEXT REFERENCES observations(id),before_data TEXT,after_data TEXT NOT NULL,source TEXT NOT NULL,created TEXT);
            CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,request TEXT NOT NULL,result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS varieties(id TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS variety_links(observation_id TEXT PRIMARY KEY REFERENCES observations(id),variety_id TEXT REFERENCES varieties(id),status TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS item_links(observation_id TEXT REFERENCES observations(id),item_id TEXT REFERENCES items(id),PRIMARY KEY(observation_id,item_id));
            CREATE TABLE IF NOT EXISTS lineage(parent_id TEXT REFERENCES observations(id),child_id TEXT REFERENCES observations(id),reason TEXT,PRIMARY KEY(parent_id,child_id));
            CREATE TABLE IF NOT EXISTS migrations(source_key TEXT PRIMARY KEY,report TEXT NOT NULL,created TEXT);
            CREATE TABLE IF NOT EXISTS attention_evidence(id TEXT PRIMARY KEY,data TEXT NOT NULL,created TEXT NOT NULL);
            ''')
            c.execute("INSERT OR IGNORE INTO meta VALUES('schema_version','1')")
            c.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
        return {'root':str(self.root),'initialized':True}
    def connect(self):
        c=sqlite3.connect(self.db,timeout=60,factory=ClosingConnection);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');return c
    def add_attention(self,evidence):
        """Append immutable, explicitly reviewed evidence; reruns are idempotent."""
        e=dict(evidence)
        required=('id','observation_id','base_revision','review_status','kind','source','date','basis','conditions')
        if any(k not in e for k in required):raise ValueError('Evidence fields missing: '+', '.join(required))
        if e['review_status'] not in ('pending','approved','rejected'):raise ValueError('Invalid review status')
        if not isinstance(e['conditions'],dict) or not e['conditions']:raise ValueError('Explicit applicability conditions required')
        with self.tx() as c:
            r=c.execute('SELECT * FROM observations WHERE id=?',(e['observation_id'],)).fetchone()
            if not r:raise ValueError('Unknown observation ID')
            existing=c.execute('SELECT data FROM attention_evidence WHERE id=?',(e['id'],)).fetchone()
            if existing:
                if existing[0]!=dump(e):raise Conflict('Evidence ID already exists with different content')
                return e['id']
            if e['base_revision']!=r['revision']:raise Conflict('Observation changed; review evidence again')
            c.execute('INSERT INTO attention_evidence VALUES(?,?,?)',(e['id'],dump(e),now()))
        return e['id']
    @contextlib.contextmanager
    def tx(self):
        with LOCK,self.connect() as c:
            c.execute('BEGIN IMMEDIATE');yield c
    def safe(self,relative):
        p=(self.root/relative).resolve()
        if not p.is_relative_to(self.root) or p==self.root:raise ValueError('文件路径越界')
        return p
    def asset(self,c,data,ext):
        h=digest(data);rel=f'assets/{h[:2]}/{h}.{ext.lower().lstrip(".")}'
        p=self.safe(rel)
        if not p.exists():atomic(p,data)
        elif digest(p.read_bytes())!=h:raise ValueError('资源校验失败')
        c.execute('INSERT OR IGNORE INTO assets VALUES(?,?,?)',(rel,h,len(data)));return rel
    def copy_asset(self,c,path):
        p=Path(path);return self.asset(c,p.read_bytes(),p.suffix)
    def sequence(self,c,key):
        r=c.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone();n=int(r[0]) + 1 if r else 1
        c.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,str(n)));return n
    def photo(self,c,data,name,batch,label=None):
        sha=digest(data);r=c.execute('SELECT * FROM photos WHERE sha=?',(sha,)).fetchone()
        if r:
            aliases=json.loads(r['aliases'])
            if name not in aliases: aliases.append(name);c.execute('UPDATE photos SET aliases=? WHERE id=?',(dump(aliases),r['id']))
            return dict(r),True,[]
        with Image.open(io.BytesIO(data)) as im:
            im.load();w,h=ImageOps.exif_transpose(im).size;ph=phash(im);ext={'JPEG':'jpg','PNG':'png','WEBP':'webp','TIFF':'tif'}.get(im.format)
            if not ext:raise ValueError('支持 JPG、PNG、WebP、TIFF')
        possible=[{'photo':r['label'],'distance':(int(ph,16)^int(r['phash'],16)).bit_count()} for r in c.execute('SELECT label,phash FROM photos') if (int(ph,16)^int(r['phash'],16)).bit_count()<=8]
        label=label or f'P{self.sequence(c,"photo_sequence"):06d}';id=uid();path=self.asset(c,data,ext)
        c.execute('INSERT INTO photos VALUES(?,?,?,?,?,?,?,?,?,?)',(id,label,sha,path,dump([name]),w,h,batch,ph,now()))
        return dict(c.execute('SELECT * FROM photos WHERE id=?',(id,)).fetchone()),False,possible
    def import_photo(self,path=None,*,data=None,name=None,batch='新增照片'):
        if path:data=Path(path).read_bytes();name=Path(path).name
        with self.tx() as c:r,duplicate,possible=self.photo(c,data,name,batch)
        return {'id':r['id'],'label':r['label'],'duplicate':duplicate,'possible_duplicates':possible}
    def get(self,c,id):
        r=c.execute('SELECT * FROM observations WHERE id=? OR label=?',(id,id)).fetchone()
        if not r:raise ValueError('观察记录不存在')
        return r
    def history(self,c,id,before,after,source):
        c.execute('INSERT INTO history VALUES(?,?,?,?,?,?)',(uid(),id,dump(before) if before is not None else None,dump(after),source,now()))
    def save(self,c,r,data,source):
        before=json.loads(r['data']);self.history(c,r['id'],before,data,source)
        c.execute('UPDATE observations SET data=?,revision=revision+1 WHERE id=?',(dump(data),r['id']))
    def operation(self,c,key,request):
        if not key:raise ValueError('缺少操作编号')
        r=c.execute('SELECT * FROM operations WHERE id=?',(key,)).fetchone()
        if r and r['request']!=dump(request):raise Conflict('操作编号已用于不同请求')
        return json.loads(r['result']) if r else None
    def done(self,c,key,req,result):
        c.execute('INSERT INTO operations VALUES(?,?,?)',(key,dump(req),dump(result)));return result
    def validate_fields(self,changes,allowed):
        if not isinstance(changes,dict) or not changes or set(changes)-allowed:raise ValueError('包含不允许修改的字段')
        if 'identity' in changes and changes['identity'] not in ['已确认','较可能','待确认']:raise ValueError('辨认可信度无效')
        for k in ('quantity','physical_quantity'):
            if k in changes and changes[k] is not None and (type(changes[k]) is not int or changes[k]<0):raise ValueError('数量必须是非负整数或空值')
        for k in PRICE&changes.keys()-{'price_unit','price_source','quote_date','access_date','valuation','confidence','evidence'}:
            if changes[k] is not None and (type(changes[k]) not in (int,float) or not math.isfinite(changes[k]) or changes[k]<0):raise ValueError('价格必须为非负数或空值')
        if 'orientation' in changes:
            o=changes['orientation']
            if not isinstance(o,dict) or not math.isfinite(float(o.get('angle_deg',float('nan')))):raise ValueError('朝向需要有限角度')
        dump(changes)
    def derived_images(self,c,data,angle):
        with Image.open(self.safe(data['source_crop'])) as im:rot=orient(im,angle)
        high=self.asset(c,image_bytes(rot),'png');small=rot.convert('RGBA');small.thumbnail((700,500),Image.Resampling.LANCZOS)
        white=Image.new('RGBA',small.size,'white');white.alpha_composite(small)
        prev=self.asset(c,image_bytes(white.convert('RGB'),'JPEG'),'jpg')
        data.update(high_image=high,preview=prev)
        data['orientation'].update(image_size=list(rot.size),thumb_size=list(small.size),source_sha256=digest(self.safe(data['source_crop']).read_bytes()))
    def apply_fields(self,c,r,changes,source):
        data=json.loads(r['data']);data.update(changes)
        if 'orientation' in changes:self.derived_images(c,data,changes['orientation']['angle_deg'])
        for old in c.execute('SELECT * FROM candidates WHERE observation_id=?',(r['id'],)).fetchall():
            decisions=json.loads(old['decisions']);payload=json.loads(old['data']);updated=False
            for key in changes:
                if decisions.get(key)=='accept' and payload['changes'][key]!=changes[key]:decisions[key]='superseded';updated=True
            if updated:
                status='待复核' if 'pending' in decisions.values() else ('已采用' if 'accept' in decisions.values() else '已被后续修订替代')
                c.execute('UPDATE candidates SET decisions=?,status=? WHERE id=?',(dump(decisions),status,old['id']))
        self.save(c,r,data,source);return r['revision']+1
    def edit(self,id,changes,revision,op):
        self.validate_fields(changes,PERSONAL|IDENTITY|PRICE|{'orientation','quantity'})
        req={'id':id,'changes':changes,'revision':revision,'type':'edit'}
        with self.tx() as c:
            old=self.operation(c,op,req)
            if old:return old
            r=self.get(c,id)
            if r['revision']!=revision:raise Conflict('记录已更新，请刷新后重新修改')
            return self.done(c,op,req,{'revision':self.apply_fields(c,r,changes,'用户直接编辑'),'saved':True})
    def proposal(self,c,task,id,base,changes,provenance):
        self.validate_fields(changes,MODEL_FIELDS)
        if not provenance.get('basis') or not provenance.get('method'):raise ValueError('候选必须附判断依据和检索方式')
        if '图搜图' in provenance['method'] and not (provenance.get('uploaded') is True and provenance.get('upload_evidence')):raise ValueError('以图搜图必须附真实上传证据；不能冒充图片上传')
        r=self.get(c,id)
        if base!=r['revision']:raise Conflict('分析输入版本已过期，请重新导出任务')
        cid=uid();c.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?)',(cid,task,r['id'],base,dump({'changes':changes,'provenance':provenance}),dump({k:'pending' for k in changes}),'待复核',now()));return cid
    def request_analysis(self,id,kind='research',params=None,analyzer=None,result=None,rerun=False,request_id=None):
        params=params or {};analyzer=analyzer or (result or {}).get('analyzer') or {'name':'人工查证接口','version':VERSION,'model':'未知','prompt_version':'1'}
        # Result checksum also identifies imported external evidence. It never promotes official data.
        with self.tx() as c:
            r=self.get(c,id);data=json.loads(r['data']);req={'observation':r['id'],'input_revision':r['revision'],'input_sha':digest(dump(data).encode()),'kind':kind,'params':params,'analyzer':analyzer,'result_sha':digest(dump(result).encode()) if result else None}
            key=digest(dump(req).encode());opreq={**req,'rerun':rerun}
            if request_id:
                old=self.operation(c,request_id,opreq)
                if old:return old
            prev=c.execute('SELECT * FROM tasks WHERE task_key=? ORDER BY attempt DESC LIMIT 1',(key,)).fetchone()
            if prev and not rerun and prev['status'] in ('completed','awaiting_result'):
                out={'task':prev['id'],'cached':True,'status':prev['status'],'result':json.loads(prev['result'])}
            else:
                task=uid();attempt=prev['attempt']+1 if prev else 1
                c.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,key,attempt,'running',dump(req),None,None,now(),now()))
                if result:
                    if result.get('observation_id') not in (id,r['id'],r['label']) or result.get('input_revision')!=r['revision']:raise Conflict('候选观察编号或输入版本不匹配')
                    cid=self.proposal(c,task,r['id'],r['revision'],result['changes'],result['provenance']);res={'candidate':cid};status='completed'
                else:
                    res={'observation_id':r['id'],'label':r['label'],'input_revision':r['revision'],'input_sha':req['input_sha'],'image':data.get('high_image'), 'clues':{k:data.get(k) for k in ('name','code','face','basis','unresolved')},'result_template':{'observation_id':r['id'],'input_revision':r['revision'],'changes':{'name':data['name']},'provenance':{'basis':'填写匹配依据','method':'文字检索/目录对照','sources':[],'uploaded':False}}};status='awaiting_result'
                c.execute('UPDATE tasks SET status=?,result=?,updated=? WHERE id=?',(status,dump(res),now(),task));out={'task':task,'cached':False,'status':status,'result':res}
            return self.done(c,request_id,opreq,out) if request_id else out
    def review(self,cid,decisions,op):
        req={'candidate':cid,'decisions':decisions}
        with self.tx() as c:
            old=self.operation(c,op,req)
            if old:return old
            can=c.execute('SELECT * FROM candidates WHERE id=?',(cid,)).fetchone()
            if not can:raise ValueError('候选不存在')
            state=json.loads(can['decisions']);payload=json.loads(can['data'])
            if not decisions or any(k not in state or v not in ('accept','reject') for k,v in decisions.items()):raise ValueError('复核字段或动作无效')
            if any(state[k]!='pending' and state[k]!=v for k,v in decisions.items()):raise Conflict('该字段已处理')
            todo={k:v for k,v in decisions.items() if state[k]=='pending'}
            accepted={k:payload['changes'][k] for k,v in todo.items() if v=='accept'}
            r=self.get(c,can['observation_id'])
            if accepted and can['base_revision']!=r['revision']:raise Conflict('候选基于旧版本，请重新分析；未覆盖正式记录')
            revision=r['revision']
            if accepted:revision=self.apply_fields(c,r,accepted,'采用候选 '+cid)
            state.update(todo);status='待复核' if 'pending' in state.values() else ('已采用' if 'accept' in state.values() else '已拒绝')
            c.execute('UPDATE candidates SET decisions=?,status=?,base_revision=? WHERE id=?',(dump(state),status,revision,cid))
            return self.done(c,op,req,{'saved':True,'status':status,'revision':revision})
    def undo(self,history_id,revision,op):
        req={'undo':history_id,'revision':revision}
        with self.tx() as c:
            old=self.operation(c,op,req)
            if old:return old
            h=c.execute('SELECT * FROM history WHERE id=?',(history_id,)).fetchone()
            if not h or not h['before_data']:raise ValueError('此记录不可撤销')
            r=self.get(c,h['observation_id'])
            if r['revision']!=revision:raise Conflict('记录版本已变化')
            before=json.loads(h['before_data']);after=json.loads(h['after_data']);current=json.loads(r['data'])
            for k in set(before)|set(after):
                if before.get(k)!=after.get(k):
                    if k in before:current[k]=before[k]
                    else:current.pop(k,None)
            self.save(c,r,current,'恢复历史 '+history_id)
            return self.done(c,op,req,{'revision':revision+1,'saved':True})
    def new_observation(self,c,photo,box,parents=()):
        if len(box)!=4 or any(type(x) is not int for x in box):raise ValueError('切图区域必须为4个整数')
        x1,y1,x2,y2=box
        if not (0<=x1<x2<=photo['width'] and 0<=y1<y2<=photo['height']):raise ValueError('切图区域超出原照片')
        with Image.open(self.safe(photo['path'])) as im:crop=ImageOps.exif_transpose(im).crop(box)
        source=self.asset(c,image_bytes(crop),'png');id=uid();label=f'O{self.sequence(c,"observation_sequence"):06d}'
        data={'id':label,'name':'待辨认邮票','identity':'待确认','code':'','face':'','kind':'观察组','quantity':1,'physical_quantity':None,'location':'','notes':'','box':box,'photo':photo['label'],'category':'stamp','source_crop':source,'orientation':{'angle_deg':0,'reason':'保留原始朝向，待复核','review_status':'待复核'},'topic':'其他题材（待细分）','region':'待确认','price':None,'related':'','created_origin':'用户确认切图区域'}
        self.derived_images(c,data,0)
        c.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?)',(id,label,photo['id'],1,dump(data),1,now()));self.history(c,id,None,data,'确认切图区域')
        for parent in parents:c.execute('INSERT INTO lineage VALUES(?,?,?)',(parent,id,'用户确认切图拆分/合并'))
        return label
    def extraction(self,photo_id,boxes=None,rerun=False,analyzer=None):
        # Detection/manual boxes are candidate geometry, not an inventory mutation.
        with self.tx() as c:
            p=c.execute('SELECT * FROM photos WHERE id=? OR label=?',(photo_id,photo_id)).fetchone()
            if not p:raise ValueError('照片不存在')
            req={'photo':p['id'],'sha':p['sha'],'kind':'extract','boxes':boxes,'analyzer':analyzer or {'name':'manual-regions','version':VERSION}}
            key=digest(dump(req).encode());prev=c.execute('SELECT * FROM tasks WHERE task_key=? ORDER BY attempt DESC LIMIT 1',(key,)).fetchone()
            if prev and not rerun:return {'task':prev['id'],'cached':True,'result':json.loads(prev['result'])}
            if not boxes:raise ValueError('请提供切图区域，或使用 detect 命令运行本地检测模型')
            for box in boxes:
                if len(box)!=4 or any(type(v) is not int for v in box) or not (0<=box[0]<box[2]<=p['width'] and 0<=box[1]<box[3]<=p['height']):raise ValueError('无效切图区域')
            task=uid();res={'photo':p['id'],'boxes':boxes,'accepted':False}
            c.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,key,(prev['attempt']+1 if prev else 1),'awaiting_review',dump(req),dump(res),None,now(),now()))
            return {'task':task,'cached':False,'result':res}
    def accept_extraction(self,task,parents,op):
        req={'extract':task,'parents':parents}
        with self.tx() as c:
            old=self.operation(c,op,req)
            if old:return old
            t=c.execute('SELECT * FROM tasks WHERE id=?',(task,)).fetchone()
            if not t:raise ValueError('任务不存在')
            res=json.loads(t['result'])
            if res.get('accepted'):return self.done(c,op,req,res)
            p=c.execute('SELECT * FROM photos WHERE id=?',(res['photo'],)).fetchone();pr=[]
            for entry in parents:
                r=self.get(c,entry['id'])
                if r['photo_id']!=p['id'] or r['revision']!=entry['revision'] or not r['active']:raise Conflict('父记录不属于该照片、已归档或版本发生变化')
                pr.append(r)
            labels=[self.new_observation(c,p,b,[r['id'] for r in pr]) for b in res['boxes']]
            for r in pr:
                self.history(c,r['id'],json.loads(r['data']),json.loads(r['data']),'拆分/合并后保留为归档记录')
                c.execute('UPDATE observations SET active=0,revision=revision+1 WHERE id=?',(r['id'],))
            res.update(accepted=True,observations=labels);c.execute('UPDATE tasks SET status=?,result=?,updated=? WHERE id=?',('completed',dump(res),now(),task))
            return self.done(c,op,req,res)
    def link(self,kind,ids,data,op,target=None):
        if kind not in ('item','variety'):raise ValueError('关联类型无效')
        req={'kind':kind,'ids':ids,'data':data,'target':target}
        with self.tx() as c:
            old=self.operation(c,op,req)
            if old:return old
            rows=[self.get(c,i) for i in ids]
            if not rows:raise ValueError('必须选择观察记录')
            table='items' if kind=='item' else 'varieties';target=target or uid()
            if kind=='item' and (type(data.get('quantity')) is not int or data['quantity']<1):raise ValueError('请明确填写确认的实物数量')
            c.execute(f'INSERT INTO {table} VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',(target,dump(data)))
            for r in rows:
                if kind=='item':c.execute('INSERT OR IGNORE INTO item_links VALUES(?,?)',(r['id'],target))
                else:c.execute('INSERT OR REPLACE INTO variety_links VALUES(?,?,?)',(r['id'],target,'用户确认'))
                self.history(c,r['id'],json.loads(r['data']),json.loads(r['data']),f'用户确认{kind}关联 {target}')
            return self.done(c,op,req,{'id':target,'saved':True})
    def snapshot(self):
        with self.connect() as c:
            photos=[dict(r) for r in c.execute('SELECT * FROM photos ORDER BY label')]
            rows=[]
            for r in c.execute('SELECT * FROM observations ORDER BY label'):
                d=json.loads(r['data']);d.update(uid=r['id'],id=r['label'],revision=r['revision'],photo_uid=r['photo_id'],active=bool(r['active']));rows.append(d)
            config=json.loads(c.execute("SELECT value FROM meta WHERE key='preferences'").fetchone()[0]) if c.execute("SELECT 1 FROM meta WHERE key='preferences'").fetchone() else {'topics':[]}
            tables={t:[dict(r) for r in c.execute('SELECT * FROM '+t)] for t in ('candidates','tasks','history','varieties','variety_links','items','item_links','lineage','assets')}
            tables['attention_evidence']=[dict(r) for r in c.execute('SELECT * FROM attention_evidence ORDER BY id')] if c.execute("SELECT 1 FROM sqlite_master WHERE name='attention_evidence'").fetchone() else []
        for r in rows:
            # Use topic selection explicitly; classification never overwrites identity/notes.
            matches=[t for t in config['topics'] if re.search(t[2],r.get('name',''))]
            chosen=next((t for t in config['topics'] if t[1]==r.get('topic')),None) or (min(matches,key=lambda t:(t[0],config['topics'].index(t))) if matches else [2,'其他题材（待细分）',''])
            r['level'],r['topic']=chosen[:2];r['topic_order']=next((i for i,t in enumerate(config['topics']) if t[1]==r['topic']),99)
            r['series']=r.get('series') or r.get('code') or r['id']
        groups=collections.defaultdict(list)
        for r in rows:
            if r['active'] and r.get('category')=='stamp':groups[(r.get('name',''),r.get('face',''),r.get('code',''))].append(r)
        frequency=[]
        for key,rs in groups.items():
            # Group IDs are derived display values, not permanent variety identities.
            gid='G'+digest(dump(key).encode())[:12];total=sum(r.get('quantity') or 0 for r in rs)
            for r in rs:r['observed']=total;r['group']=gid
            frequency.append({'group':gid,'name':key[0],'face':key[1],'code':key[2],'total':total,'ids':[r['id'] for r in rs],'representative':rs[0]['id']})
        rows.sort(key=lambda r:(not r['active'],r.get('category')!='stamp',r['level'],r['topic_order'],r['series'],-r.get('set_score',0),-r.get('integrity_score',0),-r.get('condition_score',0),-r.get('postmark_score',0),r.get('observed',0),r['id']))
        for i,r in enumerate(rows,1):r['rank']=i
        counts=collections.Counter(r.get('identity','待确认') for r in rows if r.get('category')=='stamp' and r['active'])
        return {'schema_version':2,'tool_version':VERSION,'exported_at':now(),'records':rows,'photos':photos,'frequency':sorted(frequency,key=lambda f:(f['total'],f['group'])),'counts':dict(counts),'preferences':config,**tables}
    def verify(self):
        errors=[]
        with self.connect() as c:
            check=c.execute('PRAGMA integrity_check').fetchone()[0]
            if check!='ok':errors.append(check)
            errors += [str(tuple(r)) for r in c.execute('PRAGMA foreign_key_check')]
            assets=list(c.execute('SELECT * FROM assets'));rows=list(c.execute('SELECT * FROM observations'))
            for a in assets:
                p=self.safe(a['path'])
                if not p.is_file() or digest(p.read_bytes())!=a['sha']:errors.append('资源丢失或损坏 '+a['path'])
            paths={a['path'] for a in assets}
            for r in rows:
                d=json.loads(r['data'])
                for k in ('source_crop','high_image','preview'):
                    if d.get(k) not in paths:errors.append(r['label']+' 缺少 '+k)
        s=self.snapshot();return {'ok':not errors,'errors':errors,'records':len(rows),'active':sum(r['active'] for r in s['records']),'stamps':sum(r.get('category')=='stamp' for r in s['records']),'photos':len(s['photos']),'identity_counts':s['counts'],'assets_checked':len(assets)}
    def export(self,format,destination):
        target=Path(destination).resolve()
        if target.exists():raise ValueError('目标已存在；请选择新文件或目录，不覆盖旧成果')
        s=self.snapshot()
        from display import build
        s['display']=build(s)
        if format=='json':atomic(target,dump(s).encode());return str(target)
        if format=='html':
            target.mkdir(parents=True)
            try:
                for name in ('index.html','app.js','style.css'):shutil.copy2(APP/'offline'/name,target/name)
                # JSON-as-JS supports file:// without fetch/CORS and escapes script delimiters.
                atomic(target/'snapshot.js',('window.STAMP_DATA='+dump(s).replace('<','\\u003c')+';').encode())
                needed={r[k] for r in s['records'] for k in ('preview','high_image')}|{p['path'] for p in s['photos']}
                for rel in needed:p=target/rel;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(self.safe(rel),p)
            except Exception:raise
            return str(target/'index.html')
        if format=='xlsx':
            if not (APP/'runtime.json').is_file():
                raise ValueError('Excel export is optional and requires a local runtime.json (node path) and @oai/artifact-tool. Use HTML or JSON export without this setup.')
            runtime=jsonfile(APP/'runtime.json');temp=Path(tempfile.mkdtemp(prefix='stamp-export-'))
            try:
                for photo in s['photos']:
                    with Image.open(self.safe(photo['path'])) as im:
                        im=ImageOps.exif_transpose(im).convert('RGB');im.thumbnail((1600,1600),Image.Resampling.LANCZOS)
                        dest=temp/(photo['id']+'.jpg');atomic(dest,image_bytes(im,'JPEG'));photo['export_preview']=str(dest)
                atomic(temp/'data.json',dump(s).encode())
                proc=subprocess.run([runtime['node'],str(APP/'export_excel.mjs'),str(temp/'data.json'),str(self.root),str(target)],capture_output=True,text=True,encoding='utf8')
                if not target.exists() or 'EXPORTED' not in proc.stdout:raise RuntimeError(proc.stderr[-2000:] or proc.stdout[-2000:])
                from xlsx_media import deduplicate
                deduplicate(target)
            finally:shutil.rmtree(temp)
            return str(target)
        raise ValueError('导出格式无效')
    def backup(self,destination):
        target=Path(destination)
        if target.exists():raise ValueError('备份目标已存在')
        with LOCK,tempfile.TemporaryDirectory() as td:
            snap=Path(td)/'collection.sqlite3'
            with self.connect() as c,sqlite3.connect(snap,factory=ClosingConnection) as out:c.backup(out)
            with sqlite3.connect(snap,factory=ClosingConnection) as c:rels=[r[0] for r in c.execute('SELECT path FROM assets')]
            files={'collection.sqlite3':snap,**{r:self.safe(r) for r in rels}}
            checks={n:digest(p.read_bytes()) for n,p in files.items()}
            tmp=Path(td)/'backup.zip'
            with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED) as z:
                for n,p in files.items():z.write(p,n)
                z.writestr('checksums.json',dump(checks))
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(tmp,target)
        return str(target.resolve())
    @staticmethod
    def restore(archive,destination):
        target=Path(destination).resolve()
        if target.exists():raise ValueError('恢复必须使用不存在的新目录')
        with zipfile.ZipFile(archive) as z:
            checks=json.loads(z.read('checksums.json'))
            if 'collection.sqlite3' not in checks:raise ValueError('备份缺少数据库')
            for n,h in checks.items():
                if not (target/n).resolve().is_relative_to(target) or Path(n).is_absolute():raise ValueError('备份路径越界')
                if digest(z.read(n))!=h:raise ValueError('备份校验失败 '+n)
            target.mkdir(parents=True)
            for n in checks:atomic(target/n,z.read(n))
        report=Store(target).verify()
        if not report['ok']:raise ValueError('恢复后校验失败 '+dump(report))
        return report

def main():
    p=argparse.ArgumentParser(description='本地邮票收藏工具');p.add_argument('--root',default=str(APP/'data'));sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('init');sub.add_parser('verify')
    m=sub.add_parser('migrate');m.add_argument('source',help='旧工作区目录')
    a=sub.add_parser('import');a.add_argument('files',nargs='+');a.add_argument('--batch',default='新增照片')
    a=sub.add_parser('analyze');a.add_argument('id');a.add_argument('--result');a.add_argument('--rerun',action='store_true');a.add_argument('--output')
    a=sub.add_parser('extract');a.add_argument('photo');a.add_argument('--boxes',required=True);a.add_argument('--rerun',action='store_true')
    a=sub.add_parser('detect');a.add_argument('photo');a.add_argument('--model',required=True);a.add_argument('--rerun',action='store_true')
    a=sub.add_parser('review');a.add_argument('--candidate');a.add_argument('--decisions');a.add_argument('--task');a.add_argument('--parents',default='[]');a.add_argument('--operation',default=None)
    a=sub.add_parser('export');a.add_argument('format',choices=['json','html','xlsx']);a.add_argument('destination')
    a=sub.add_parser('backup');a.add_argument('destination')
    a=sub.add_parser('restore');a.add_argument('archive');a.add_argument('destination')
    a=sub.add_parser('serve');a.add_argument('--port',type=int,default=8765);a.add_argument('--no-browser',action='store_true')
    args=p.parse_args();s=Store(args.root);cmd=args.command
    if cmd=='init':out=s.init()
    elif cmd=='migrate':
        from migrate import migrate
        out=migrate(s,args.source)
    elif cmd=='import':out=[s.import_photo(f,batch=args.batch) for f in args.files]
    elif cmd=='analyze':
        out=s.request_analysis(args.id,result=jsonfile(args.result) if args.result else None,rerun=args.rerun)
        if args.output:
            target=Path(args.output)
            if target.exists():raise ValueError('任务包目标已存在')
            target.mkdir(parents=True);atomic(target/'task.json',dump(out).encode())
            if out['result'].get('image'):shutil.copy2(s.safe(out['result']['image']),target/'stamp.png')
            if out['result'].get('result_template'):atomic(target/'result-template.json',dump(out['result']['result_template']).encode())
    elif cmd=='extract':out=s.extraction(args.photo,json.loads(args.boxes),args.rerun)
    elif cmd=='detect':
        from detector import detect
        out=detect(s,args.photo,args.model,args.rerun)
    elif cmd=='review':
        if args.candidate:out=s.review(args.candidate,json.loads(args.decisions),args.operation or uid())
        elif args.task:out=s.accept_extraction(args.task,json.loads(args.parents),args.operation or uid())
        else:out=s.snapshot()['candidates']
    elif cmd=='export':out=s.export(args.format,args.destination)
    elif cmd=='backup':out=s.backup(args.destination)
    elif cmd=='restore':out=Store.restore(args.archive,args.destination)
    elif cmd=='verify':out=s.verify()
    elif cmd=='serve':
        from server import serve
        serve(s,args.port,not args.no_browser);return
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,sqlite3.Error) as e:print('错误：'+str(e),file=sys.stderr);sys.exit(1)
