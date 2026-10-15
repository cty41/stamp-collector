from pathlib import Path
import json,sys,traceback,webbrowser
from urllib.request import urlopen
from collection import APP,Store
from server import serve
if __name__=='__main__':
    try:
        store=Store(APP/'data')
        try:
            with urlopen('http://127.0.0.1:8765/api/info',timeout=2) as r:info=json.load(r)
            if info.get('root')==str(store.root):webbrowser.open('http://127.0.0.1:8765/');sys.exit(0)
        except Exception:pass
        logs=APP/'logs';logs.mkdir(exist_ok=True)
        sys.stdout=sys.stderr=open(logs/'server.log','a',encoding='utf8',buffering=1)
        serve(store,8765,True)
    except Exception:
        (APP/'启动错误.txt').write_text(traceback.format_exc(),encoding='utf8')
        raise
