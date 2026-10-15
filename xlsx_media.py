"""Lossless ZIP-media deduplication; cells, rows and image anchors are untouched."""
import zipfile,hashlib,posixpath,tempfile,os
from pathlib import Path
from xml.etree import ElementTree as E
def deduplicate(file):
    p=Path(file);fd,tmp=tempfile.mkstemp(suffix='.xlsx',dir=p.parent);os.close(fd)
    try:
        with zipfile.ZipFile(p) as z:
            hashes={};aliases={}
            for n in z.namelist():
                if n.startswith('xl/media/'):
                    h=hashlib.sha256(z.read(n)).hexdigest()
                    if h in hashes:aliases[n]=hashes[h]
                    else:hashes[h]=n
            with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED) as out:
                for inf in z.infolist():
                    if inf.filename in aliases:continue
                    b=z.read(inf.filename)
                    if inf.filename.endswith('.rels'):
                        root=E.fromstring(b);changed=False;base=posixpath.dirname(posixpath.dirname(inf.filename))
                        for r in root:
                            t=r.get('Target','');resolved=posixpath.normpath(posixpath.join(base,t)) if not t.startswith('/') else t[1:]
                            if resolved in aliases:r.set('Target',posixpath.relpath(aliases[resolved],base));changed=True
                        if changed:b=E.tostring(root,encoding='utf-8',xml_declaration=True)
                    out.writestr(inf,b)
        os.replace(tmp,p)
    finally:
        if Path(tmp).exists():Path(tmp).unlink()
