"""Read-only presentation grouping. Never changes observations or their identities."""
import json,hashlib,math
from pathlib import Path

CONFIG=Path(__file__).with_name('display_groups.json')
def signature(r):
    fields={k:r.get(k) for k in ('name','code','face','kind','category','preview')}
    return hashlib.sha256(json.dumps(fields,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def interest(g):
    return (g['level'],g['topic_order'],-g['integrity_score'],-g['condition_score'],-g['postmark_score'],g['members'][0])
def rare_key(g):
    return ({'single':0,'whole':1,'other':2}[g['category']],g['quantity'] is None,g['quantity'] if g['quantity'] is not None else math.inf,*interest(g))

def build(snapshot,config=None):
    config=config if config is not None else (json.loads(CONFIG.read_text(encoding='utf8')) if CONFIG.exists() else {'groups':[]})
    records={r['id']:r for r in snapshot['records'] if r['active']};used=set();groups=[];warnings=[]
    def make(entry,rs,reviewed):
        rep=entry.get('representative');rep=rep if rep in [r['id'] for r in rs] else rs[0]['id'];r=records[rep]
        category=entry.get('category') or ('other' if r['category']!='stamp' else 'whole' if r.get('kind') in ('小型张','小本票/册页','贴票纸片') else 'single')
        quantities={a['id']:(entry.get('quantities',{}).get(a['id'],a.get('quantity')) if category=='single' else entry.get('quantities',{}).get(a['id'],1)) for a in rs}
        confirmed=0
        # Only explicitly confirmed item links with matching complete quantities can deduplicate.
        uid_to_label={a['uid']:a['id'] for a in rs};claimed=set()
        for item in snapshot.get('items',[]):
            labels={uid_to_label[l['observation_id']] for l in snapshot.get('item_links',[]) if l['item_id']==item['id'] and l['observation_id'] in uid_to_label}
            q=json.loads(item['data']).get('quantity')
            if len(labels)>1 and not labels&claimed and type(q) is int and all(quantities[l]==q for l in labels):
                first=sorted(labels)[0]
                for label in labels-{first}:quantities[label]=0;confirmed+=1
                claimed|=labels
        qvals=list(quantities.values());quantity=sum(qvals) if all(type(q) is int and q>=0 for q in qvals) else None
        quotes=[{'id':a['id'],'value':a['price'],'unit':a.get('price_unit','单位待核'),'source':a.get('price_source',''),'date':a.get('quote_date')} for a in rs if a.get('price') is not None]
        # Rank by the highest documented member quote; never multiply or call it a group valuation.
        price=max((q['value'] for q in quotes),default=None)
        g={k:r.get(k) for k in ('name','code','face','preview','topic','series','level','topic_order')}
        g.update(id=entry['id'],members=sorted(a['id'] for a in rs),representative=rep,category=category,quantity=quantity,unit='枚' if category=='single' else '件',quotes=quotes,price=price,reviewed=reviewed,group_basis=entry.get('basis','新增或修改记录，尚未复核展示分组'),confirmed_duplicate_views=confirmed,possible_duplicate=len({a['photo_uid'] for a in rs})>1 and confirmed<len(rs)-1,identities=sorted({a.get('identity','待确认') for a in rs}),regions=sorted({a.get('region') or a.get('country') or '待确认' for a in rs}),topics=sorted({a['topic'] for a in rs}),series_values=sorted({a.get('series','') for a in rs}),description=r.get('commemorative') or r.get('tags') or r.get('topic') or '',integrity_score=max(a.get('integrity_score',0) for a in rs),condition_score=max(a.get('condition_score',0) for a in rs),postmark_score=max(a.get('postmark_score',0) for a in rs))
        groups.append(g)
    ids=set()
    for entry in config.get('groups',[]):
        if entry['id'] in ids:raise ValueError('展示组编号重复')
        ids.add(entry['id']);rs=[]
        for label in entry['members']:
            if label in used:raise ValueError('观察编号重复归组 '+label)
            if label not in records:continue
            r=records[label]
            if entry.get('signatures',{}).get(label)!=signature(r):warnings.append(label+' 内容已改变，暂单列待复核');continue
            used.add(label);rs.append(r)
        if rs:make(entry,rs,True)
    for label in sorted(records.keys()-used):make({'id':'NEW-'+records[label]['uid']},[records[label]],False)
    groups.sort(key=rare_key)
    for i,g in enumerate(groups,1):g['rank']=i
    assert sorted(m for g in groups for m in g['members'])==sorted(records)
    return {'version':2,'groups':groups,'warnings':warnings,'record_count':len(records),'group_count':len(groups),'rules':'本批可见数量升序；同数量按题材兴趣、完整性、品相、邮戳、原编号；组合与整件单列。'}
