"""One-way, idempotent migration; archive original inputs, never rewrite them."""
from pathlib import Path
import json
from collection import APP,dump,digest,uid,now,jsonfile

def migrate(s,source):
    import openpyxl  # read only: authored workbooks use the JS exporter
    base=Path(source).resolve();manifest=base/'outputs/邮票转正_15图/manifest.json';latest=base/'work/personal-data.json'
    excel=base/'outputs/邮票收藏整理_以图搜图查证更新_2026-10-15.xlsx'
    prefs=base/'work/philately-tool/collection_preferences.json';m=jsonfile(manifest);d=jsonfile(latest)
    key=digest((digest(manifest.read_bytes())+digest(latest.read_bytes())+digest(excel.read_bytes())).encode())
    s.init()
    with s.tx() as c:
        done=c.execute('SELECT report FROM migrations WHERE source_key=?',(key,)).fetchone()
        if done:return {**json.loads(done[0]),'cached':True}
        if c.execute('SELECT COUNT(*) FROM observations').fetchone()[0]:raise ValueError('已有档案不是这次迁移；请使用新收藏目录，避免覆盖')
        wb=openpyxl.load_workbook(excel,read_only=True,data_only=False);sheet=wb['汇总清单']
        # Locate columns by actual header, not a position guessed from this run.
        header=[sheet.cell(6,i).value for i in range(1,20)]
        values={row[0]:list(row) for row in sheet.iter_rows(min_row=7,max_col=19,values_only=True) if row[0]}
        merged={r['id']:r for r in d['all']};originals={r['id']:r for r in m['records']}
        if set(values)!=set(originals):raise ValueError('Excel 与切图清单编号不一致')
        photos={};conflicts=[]
        for p in m['photos']:
            data=(base/'outputs/邮票汇总_15图'/p['original']).read_bytes()
            if digest(data)!=p['sha256']:raise ValueError('原照片校验失败 '+p['photo'])
            photo,_,_=s.photo(c,data,p['filename'],'历史批次15图',p['photo']);photos[p['photo']]=photo
        for label,record in originals.items():
            r={**record,**merged.get(label,{})};r['category']='stamp' if label in merged else 'other'
            r['source_crop']=s.copy_asset(c,base/'outputs/邮票汇总_15图'/record['orientation']['source_crop'])
            r['high_image']=s.copy_asset(c,base/'outputs/邮票转正_15图'/record['orientation']['crop'])
            r['preview']=s.copy_asset(c,base/'work/excel-previews'/f'{label}.jpg')
            r['legacy_group']=r.get('group');r['legacy_excel_row']=values[label];r['legacy_excel_headers']=header
            differences={}
            # Verified source layout: Q/R/S are inventory quantity, location, personal notes.
            for field,index in [('physical_quantity',16),('location',17),('notes',18)]:
                before=r.get(field);v=values[label][index]
                if before in ('',None) and v in ('',None):continue
                if before!=v:differences[field]={'json':before,'excel':v}
            oid=uid();c.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?)',(oid,label,photos[r['photo']]['id'],1,dump(r),1,now()))
            s.history(c,oid,None,r,'迁移当前正式快照；历史发生时间未推断')
            if differences:
                cid=uid();changes={k:v['excel'] for k,v in differences.items()};payload={'changes':changes,'provenance':{'method':'迁移字段冲突','basis':'Excel 与 JSON 的个人字段不一致；未自动覆盖','differences':differences}}
                c.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?)',(cid,None,oid,1,dump(payload),dump({k:'pending' for k in changes}),'待复核',now()));conflicts.append({'id':label,'fields':differences})
        config=jsonfile(prefs);c.execute("INSERT OR REPLACE INTO meta VALUES('preferences',?)",(dump(config),))
        # Preserve research evidence and source snapshots as verified assets for complete backups.
        evidence={}
        for p in [manifest,latest,excel,prefs,*sorted((base/'work/reverse-search').glob('*'))]:
            if p.is_file():evidence[str(p.relative_to(base))]=s.copy_asset(c,p)
        c.execute("INSERT OR REPLACE INTO meta VALUES('migration_evidence',?)",(dump(evidence),))
        report={'records':len(originals),'stamps':len(merged),'other':len(originals)-len(merged),'photos':len(photos),'identity_counts':d['identity_counts'],'conflicts':conflicts,'source_excel_sha256':digest(excel.read_bytes()),'migration_time':now(),'cached':False}
        c.execute('INSERT INTO migrations VALUES(?,?,?)',(key,dump(report),now()))
        wb.close()
    return report
