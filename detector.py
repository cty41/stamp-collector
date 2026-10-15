"""Optional YOLO adapter. The archive/browser do not import or depend on YOLO."""
from pathlib import Path
from PIL import Image,ImageOps
import json
from collection import digest,dump,uid,now
def detect(store,photo_id,model_path,rerun=False):
    model_path=Path(model_path).resolve()
    if not model_path.is_file():raise ValueError('检测模型不存在')
    analyzer={'name':'YOLO','model_sha256':digest(model_path.read_bytes()),'confidence':0.25,'margin':0.02,'adapter_version':'1'}
    with store.connect() as c:
        photo=c.execute('SELECT * FROM photos WHERE id=? OR label=?',(photo_id,photo_id)).fetchone()
        if not photo:raise ValueError('照片不存在')
        key=digest(dump({'photo_sha':photo['sha'],'analyzer':analyzer}).encode())
    with store.tx() as c:
        prior=c.execute('SELECT * FROM tasks WHERE task_key=? ORDER BY attempt DESC LIMIT 1',(key,)).fetchone()
        if prior and not rerun and prior['status']=='completed':return {'cached':True,**json.loads(prior['result'])}
        task=uid();c.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,key,prior['attempt']+1 if prior else 1,'running',dump(analyzer),None,None,now(),now()))
    try:
        from ultralytics import YOLO
        with Image.open(store.safe(photo['path'])) as im:source=ImageOps.exif_transpose(im).convert('RGB')
        result=YOLO(str(model_path))(source,conf=.25,verbose=False)[0]
        boxes=[]
        if result.boxes is not None:
            for box in result.boxes.xyxy.tolist():
                x1,y1,x2,y2=box;mx=(x2-x1)*.02;my=(y2-y1)*.02
                boxes.append([max(0,int(x1-mx)),max(0,int(y1-my)),min(photo['width'],int(x2+mx)),min(photo['height'],int(y2+my))])
        boxes.sort(key=lambda b:(b[1],b[0]))
        if not boxes:raise ValueError('未检出区域；可以手工框选')
        out=store.extraction(photo['id'],boxes,rerun,analyzer)
        with store.tx() as c:c.execute('UPDATE tasks SET status=?,result=?,updated=? WHERE id=?',('completed',dump(out),now(),task))
        return out
    except Exception as e:
        with store.tx() as c:c.execute('UPDATE tasks SET status=?,error=?,updated=? WHERE id=?',('failed',str(e),now(),task))
        raise ValueError('检测未完成：'+str(e)+'。正式档案未变；可手工框选，或在安装检测依赖后重试。') from e
