import io,json,zipfile
from .store import encode

def bundle(store,cid):
    out=io.BytesIO()
    with store.lock:
        case=store.case(cid);records=store.records(cid)
        if sum(len(encode(r)) for r in records)>25_000_000:raise ValueError('case too large for inline export; use a stopped-service database backup')
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('case.json',json.dumps(case,indent=2))
            z.writestr('records.json',json.dumps(records,indent=2))
            events=store.db.execute('SELECT * FROM events WHERE case_id=? ORDER BY seq',(cid,)).fetchall()
            z.writestr('audit.json',json.dumps([dict(r) for r in events],indent=2))
            versions=store.db.execute('SELECT v.* FROM versions v JOIN records r ON r.id=v.id WHERE r.case_id=? ORDER BY v.id,v.version',(cid,)).fetchall()
            z.writestr('record-history.json',json.dumps([dict(r) for r in versions],indent=2))
            for r in records:
                if r['kind']=='deliverable':z.writestr(r['id']+('-STALE' if r['stale'] else '')+'.md',r['data']['markdown'])
    return out.getvalue()
