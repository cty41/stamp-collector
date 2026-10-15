"""Generate a new read-only folder, then optionally open a local file. No server."""
from pathlib import Path
import argparse,json,traceback,webbrowser
from urllib.parse import quote
import html
from collection import Store,APP,now,uid,atomic

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',default=str(APP/'data'));p.add_argument('--destination');p.add_argument('--no-open',action='store_true');a=p.parse_args()
    dest=Path(a.destination) if a.destination else APP/'exports'/('邮票清单_'+now().replace(':','-')+'_'+uid()[:6])
    entry=Store(a.root).export('html',dest)
    relative=Path(entry).resolve().relative_to(APP) if Path(entry).resolve().is_relative_to(APP) else Path(entry).resolve()
    atomic(APP/'latest_offline.json',json.dumps({'index':str(relative)},ensure_ascii=False).encode())
    url=quote(relative.as_posix(),safe='/:') if not relative.is_absolute() else relative.as_uri()
    atomic(APP/'index.html',('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>邮票清单</title><meta http-equiv="refresh" content="0;url='+html.escape(url,quote=True)+'"><p><a href="'+html.escape(url,quote=True)+'">打开邮票清单</a></p></html>').encode('utf8'))
    print(entry)
    if not a.no_open:webbrowser.open(Path(entry).as_uri())
if __name__=='__main__':
    try:main()
    except Exception:
        atomic(APP/'生成错误.txt',traceback.format_exc().encode('utf8'));raise
