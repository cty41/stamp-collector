"""Read-only presentation grouping. Never changes observations or their identities."""
import json,hashlib,math
from statistics import median
from pathlib import Path

CONFIG=Path(__file__).with_name('display_groups.json')
def signature(r):
    fields={k:r.get(k) for k in ('name','code','face','kind','category','preview')}
    return hashlib.sha256(json.dumps(fields,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def interest(g):
    return (g['level'],g['topic_order'],-g['integrity_score'],-g['condition_score'],-g['postmark_score'],g['members'][0])
def rare_key(g):
    return ({'single':0,'whole':1,'other':2}[g['category']],g['quantity'] is None,g['quantity'] if g['quantity'] is not None else math.inf,*interest(g))

def attention_key(g):
    return (g['category']=='other',not g['focus'],*interest(g)[:-1])

def reviewed_evidence(snapshot,rs):
    result=[]
    for row in snapshot.get('attention_evidence',[]):
        e=json.loads(row['data']) if 'data' in row else dict(row)
        for r in rs:
            if e.get('observation_id')!=r['uid']:continue
            conditions=e.get('conditions',{})
            valid=(r.get('identity')=='已确认' and e.get('review_status')=='approved' and e.get('applicable') is True
                   and e.get('base_revision')==r.get('revision') and bool(conditions)
                   and bool(e.get('reviewer')) and bool(e.get('reviewed_at'))
                   and all(r.get(k)==v for k,v in conditions.items())
                   and str(e.get('source','')).startswith(('https://','http://'))
                   and bool(e.get('date')) and bool(e.get('basis')))
            result.append(dict(e,member=r['id'],valid=bool(valid)))
    return result

def enrich(g,rs,snapshot):
    g['whole_form']=g['category']=='whole' or any('连' in r.get('kind','') for r in rs)
    g['form']='whole' if g['whole_form'] else g['category']
    es=reviewed_evidence(snapshot,rs);g['attention_evidence']=es
    g['focus']=any(e['valid'] and e.get('focus') is True and
        (e.get('kind')=='special' or (e.get('kind')=='market' and e.get('evidence_type') in ('成交','目录稀缺说明','专业稀缺说明'))) for e in es)
    eligible=[]
    unit='件' if g['whole_form'] else '枚'
    for e in es:
        low,high=e.get('low'),e.get('high')
        if (e['valid'] and e.get('kind')=='market' and e.get('evidence_type')=='成交'
            and e.get('currency')=='CNY' and e.get('unit')==unit
            and type(low) in (float,int) and type(high) in (float,int)
            and math.isfinite(low) and math.isfinite(high) and 0<=low<=high):
            eligible.append(dict(e,value=(low+high)/2))
    g['comparable_quotes']=eligible
    # Never pool prices from different member conditions into a fictional item.
    members=sorted({e['member'] for e in eligible})
    chosen=g['representative'] if g['representative'] in members else (members[0] if members else None)
    g['price_member']=chosen
    g['price']=median([e['value'] for e in eligible if e['member']==chosen]) if chosen else None
    g['price_unit']=unit
    g['price_status']='可比成交参考' if eligible else '待估；原价格资料不可直接比较' if g['quotes'] else '市场价值待核'
    g['research_priority']='优先查证' if g['level']==1 and any(r.get('identity')!='已确认' for r in rs) else ''
    g['sort_reason']=('重点关注（仅适用列出的成员）；' if g['focus'] else '')+g['topic']+'；'+g['price_status']
    if g['whole_form']:g['sort_reason']+='；整件保留，溢价未自动认定'

def rank_attention(groups):
    # Quantities sort only within the same quality and physical-form cohort.
    # Preserve slots across unlike forms rather than comparing pieces with stamps.
    groups.sort(key=lambda g:(*attention_key(g),g['members'][0]))
    buckets={}
    for i,g in enumerate(groups):buckets.setdefault((*attention_key(g),g['form']),[]).append(i)
    for slots in buckets.values():
        if groups[slots[0]]['whole_form']:continue
        ordered=sorted((groups[i] for i in slots),key=lambda g:(g['quantity'] is None,g['quantity'] if g['quantity'] is not None else math.inf,g['members'][0]))
        for i,g in zip(slots,ordered):groups[i]=g

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
        # Legacy quotes remain visible; only reviewed comparable sales can rank.
        price=None
        g={k:r.get(k) for k in ('name','code','face','preview','topic','series','level','topic_order')}
        g.update(id=entry['id'],members=sorted(a['id'] for a in rs),representative=rep,category=category,quantity=quantity,unit='枚' if category=='single' else '件',quotes=quotes,price=price,reviewed=reviewed,group_basis=entry.get('basis','新增或修改记录，尚未复核展示分组'),confirmed_duplicate_views=confirmed,possible_duplicate=len({a['photo_uid'] for a in rs})>1 and confirmed<len(rs)-1,identities=sorted({a.get('identity','待确认') for a in rs}),regions=sorted({a.get('region') or a.get('country') or '待确认' for a in rs}),topics=sorted({a['topic'] for a in rs}),series_values=sorted({a.get('series','') for a in rs}),description=r.get('commemorative') or r.get('tags') or r.get('topic') or '',integrity_score=max(a.get('integrity_score',0) for a in rs),condition_score=max(a.get('condition_score',0) for a in rs),postmark_score=max(a.get('postmark_score',0) for a in rs))
        enrich(g,rs,snapshot)
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
    for i,g in enumerate(sorted(groups,key=rare_key),1):g['rarity_rank']=i
    rank_attention(groups)
    for i,g in enumerate(groups,1):g['rank']=i
    for i,g in enumerate(sorted(groups,key=lambda g:(g['price'] is None,g['price_unit'],-(g['price'] or 0),g['rank'])),1):g['price_rank']=i
    assert sorted(m for g in groups for m in g['members'])==sorted(records)
    return {'version':3,'groups':groups,'warnings':warnings,'record_count':len(records),'group_count':len(groups),'rules':'收藏关注优先：审核证据、题材、完整性、品相、邮戳；同形态同条件再比较可见数量。完整连票不按内部枚数降级。数量范围为本次导出的全部照片，筛选不重算；不是库存或市场存世量。价格按枚/件分区，仅比较审核通过的人民币成交；缺价与不可比资料列待估。'}
