from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,unquote
from pathlib import Path
import base64,json,mimetypes,secrets,threading,webbrowser
from collection import APP,Store,Conflict,dump,uid,now,atomic

def make_server(store,port=8765):
    token=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,data,ctype='application/json; charset=utf-8'):
            if isinstance(data,(dict,list)):data=dump(data).encode()
            if isinstance(data,str):data=data.encode()
            self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' data: blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'");self.end_headers();self.wfile.write(data)
        def valid_host(self):return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')
        def do_GET(self):
            try:
                if not self.valid_host():return self.send(403,{'error':'Host rejected'})
                p=unquote(urlsplit(self.path).path)
                if p=='/api/info':return self.send(200,{'root':str(store.root),'version':'1.0.0'})
                if p=='/api/snapshot':return self.send(200,{**store.snapshot(),'session_token':token})
                if p=='/snapshot.js':return self.send(200,'window.ARCHIVE_SNAPSHOT=null;','text/javascript; charset=utf-8')
                if p in ('/','/index.html','/app.js','/style.css'):
                    f=APP/'web'/('index.html' if p=='/' else p[1:]);return self.send(200,f.read_bytes(),mimetypes.guess_type(f)[0] or 'text/plain')
                if p.startswith('/assets/'):
                    with store.connect() as c: registered=c.execute('SELECT 1 FROM assets WHERE path=?',(p[1:],)).fetchone()
                    if not registered:return self.send(404,{'error':'资源未登记'})
                    f=store.safe(p[1:]);return self.send(200,f.read_bytes(),mimetypes.guess_type(f)[0] or 'application/octet-stream')
                return self.send(404,{'error':'不存在'})
            except (ValueError,OSError) as e:self.send(404,{'error':str(e)})
        def do_POST(self):
            try:
                origin=self.headers.get('Origin');allowed={f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}
                if not self.valid_host() or origin not in allowed or self.headers.get('X-Archive-Token')!=token:return self.send(403,{'error':'写入来源或会话令牌无效'})
                if not self.headers.get('Content-Type','').startswith('application/json'):return self.send(415,{'error':'只接受JSON'})
                n=int(self.headers.get('Content-Length','0'))
                if n<=0 or n>100*1024*1024:return self.send(413,{'error':'请求大小无效'})
                a=json.loads(self.rfile.read(n));p=urlsplit(self.path).path
                if p=='/api/edit':out=store.edit(a['id'],a['changes'],a['revision'],a['operation'])
                elif p=='/api/review':out=store.review(a['candidate'],a['decisions'],a['operation'])
                elif p=='/api/undo':out=store.undo(a['history'],a['revision'],a['operation'])
                elif p=='/api/import':out=store.import_photo(data=base64.b64decode(a['data'],validate=True),name=Path(a['name']).name,batch=a.get('batch','网页导入'))
                elif p=='/api/analyze':out=store.request_analysis(a['id'],result=a.get('result'),rerun=a.get('rerun',False),request_id=a.get('operation'))
                elif p=='/api/extract':out=store.extraction(a['photo'],a['boxes'],a.get('rerun',False))
                elif p=='/api/accept-extraction':out=store.accept_extraction(a['task'],a.get('parents',[]),a['operation'])
                elif p=='/api/link':out=store.link(a['kind'],a['ids'],a['data'],a['operation'],a.get('target'))
                elif p=='/api/verify':out=store.verify()
                elif p=='/api/export':
                    fmt=a['format'];dest=store.root.parent/'exports'/('收藏_'+now().replace(':','-')+'_'+uid()[:6]+('.'+fmt if fmt!='html' else ''))
                    out={'path':store.export(fmt,dest)}
                elif p=='/api/backup':out={'path':store.backup(store.root.parent/'backups'/('收藏备份_'+now().replace(':','-')+'.zip'))}
                else:return self.send(404,{'error':'不存在的操作'})
                self.send(200,out)
            except Conflict as e:self.send(409,{'error':str(e)})
            except Exception as e:self.send(400,{'error':str(e)})
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)

def serve(store,port=8765,open_browser=True):
    if not store.db.exists():store.init()
    # An interrupted detector task is explicitly recoverable, never treated as completed.
    with store.tx() as c:c.execute("UPDATE tasks SET status='interrupted',error='上次运行中断；可重试',updated=? WHERE status='running'",(now(),))
    try:server=make_server(store,port)
    except OSError:server=make_server(store,0)
    url=f'http://127.0.0.1:{server.server_port}/';print('邮票收藏：'+url,flush=True)
    if open_browser:threading.Timer(.4,lambda:webbrowser.open(url)).start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
