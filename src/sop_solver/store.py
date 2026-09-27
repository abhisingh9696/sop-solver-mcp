"""Single-owner SQLite workspace: atomic revisions, idempotency and stale propagation."""
import contextlib, hashlib, json, os, sqlite3, threading, uuid
try:
    import fcntl
except ImportError:  # Windows: no fcntl; emulate flock with msvcrt byte-range locking
    import msvcrt as _msvcrt
    class _FcntlShim:
        LOCK_EX = 2
        LOCK_NB = 4
        LOCK_UN = 8
        def flock(self, f, op):
            if op & self.LOCK_UN:
                f.seek(0); _msvcrt.locking(f.fileno(), _msvcrt.LK_UNLCK, 1)
            else:
                f.seek(0)
                try:
                    _msvcrt.locking(f.fileno(), _msvcrt.LK_NBLCK, 1)
                except OSError as e:
                    raise BlockingIOError(*e.args)
    fcntl = _FcntlShim()
from pathlib import Path
from datetime import datetime,timezone
class Conflict(ValueError): pass
class NotFound(ValueError): pass
def now(): return datetime.now(timezone.utc).isoformat()
def encode(x): return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False)
def hash_value(x): return hashlib.sha256(encode(x).encode()).hexdigest()
def uid(prefix): return prefix+'_'+uuid.uuid4().hex[:20]
class Store:
    def __init__(self,path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lockfile=open(str(path)+'.lock','a')
        try: fcntl.flock(self.lockfile,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.lockfile.close(); raise RuntimeError('Another process owns this database')
        self.lock=threading.RLock();self.db=sqlite3.connect(path,check_same_thread=False,isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY,rev INTEGER,data TEXT,state TEXT,created TEXT,updated TEXT);
        CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,case_id TEXT,kind TEXT,version INTEGER,data TEXT,deps TEXT,stale INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS versions(id TEXT,version INTEGER,data TEXT,deps TEXT,at TEXT,PRIMARY KEY(id,version));
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,case_id TEXT,rev INTEGER,at TEXT,action TEXT,data TEXT);
        CREATE TABLE IF NOT EXISTS requests(scope TEXT,key TEXT,fingerprint TEXT,response TEXT,PRIMARY KEY(scope,key));
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,case_id TEXT,kind TEXT,status TEXT,spec TEXT,snapshot TEXT,result TEXT,cancel INTEGER DEFAULT 0,created TEXT);
        ''')
        self.db.execute("UPDATE jobs SET status='INTERRUPTED' WHERE status IN ('QUEUED','RUNNING')")
        os.chmod(path,0o600)
    def close(self):
        self.db.close();fcntl.flock(self.lockfile,fcntl.LOCK_UN);self.lockfile.close()
    @contextlib.contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try: yield; self.db.execute('COMMIT')
            except BaseException: self.db.execute('ROLLBACK');raise
    def case(self,id):
        with self.lock:
            row=self.db.execute('SELECT * FROM cases WHERE id=?',(id,)).fetchone()
            if row is None: raise NotFound('case not found')
            r=dict(row);r['data']=json.loads(r['data']);return r
    def record(self,case_id,id,version=None):
        with self.lock:
            row=self.db.execute('SELECT * FROM records WHERE id=? AND case_id=?',(id,case_id)).fetchone()
            if row is None: raise NotFound('record not found in this case')
            r=dict(row)
            if version is not None:
                old=self.db.execute('SELECT * FROM versions WHERE id=? AND version=?',(id,version)).fetchone()
                if old is None: raise NotFound('record version not found')
                r.update(dict(old));r['historical']=version!=row['version']
            r['data']=json.loads(r['data']);r['deps']=json.loads(r['deps']);r['stale']=bool(r['stale']);return r
    def records(self,cid,kind=None):
        with self.lock:
            rows=self.db.execute('SELECT id FROM records WHERE case_id=?'+(' AND kind=?' if kind else ''),(cid,kind) if kind else (cid,)).fetchall()
            return [self.record(cid,r['id']) for r in rows]
    def mutate(self,cid,expected,key,payload,action,fn):
        fingerprint=hash_value({'action':action,'payload':payload})
        with self.transaction():
            old=self.db.execute('SELECT * FROM requests WHERE scope=? AND key=?',(cid,key)).fetchone()
            if old:
                if old['fingerprint']!=fingerprint: raise Conflict('idempotency key reused with different request')
                return json.loads(old['response'])
            case=self.case(cid)
            if case['rev']!=expected: raise Conflict(f"STALE_REVISION: expected {expected}; current {case['rev']}")
            result=fn(); rev=case['rev']+1
            self.db.execute('UPDATE cases SET rev=?,updated=? WHERE id=?',(rev,now(),cid))
            if case['state'] in ['COMPLETED','COMPLETED_CONDITIONAL'] and action!='finalize':
                self.db.execute("UPDATE cases SET state='REVIEWING' WHERE id=?",(cid,))
            result={'case_id':cid,'revision':rev,**result}
            self.db.execute('INSERT INTO events(case_id,rev,at,action,data) VALUES(?,?,?,?,?)',(cid,rev,now(),action,encode(result)))
            self.db.execute('INSERT INTO requests VALUES(?,?,?,?)',(cid,key,fingerprint,encode(result)))
            return result
    def open(self,data,key):
        fp=hash_value(data)
        with self.transaction():
            old=self.db.execute("SELECT * FROM requests WHERE scope='OPEN' AND key=?",(key,)).fetchone()
            if old:
                if old['fingerprint']!=fp:raise Conflict('open request ID conflict')
                return json.loads(old['response'])
            id=uid('case');self.db.execute('INSERT INTO cases VALUES(?,?,?,?,?,?)',(id,0,encode(data),'INTAKE',now(),now()))
            out={'case_id':id,'revision':0,'state':'INTAKE'}
            self.db.execute('INSERT INTO requests VALUES(?,?,?,?)',('OPEN',key,fp,encode(out)));return out
    def put(self,cid,kind,data,deps,id=None):
        """Only call inside a transaction. References and cycles are checked here."""
        deps=list(dict.fromkeys(deps)); records={r['id']:r for r in self.records(cid)}
        for dep in deps:
            if dep not in records: raise NotFound('dependency not found in case: '+dep)
            if records[dep]['stale']: raise Conflict('dependency is stale: '+dep)
        id=id or uid(kind)
        def reaches(current,target,seen):
            if current==target:return True
            if current in seen:return False
            seen.add(current)
            return any(reaches(d,target,seen) for d in records.get(current,{}).get('deps',[]))
        if any(reaches(d,id,set()) for d in deps): raise Conflict('dependency cycle')
        if id in records:
            old=records[id]
            if old['kind']!=kind:raise Conflict('record kind cannot change')
            ver=old['version']+1
            affected={id};changed=True
            while changed:
                changed=False
                for r in records.values():
                    if r['id'] not in affected and set(r['deps'])&affected:
                        affected.add(r['id']);changed=True
            for rid in affected-{id}:self.db.execute('UPDATE records SET stale=1 WHERE id=?',(rid,))
            self.db.execute('UPDATE records SET version=?,data=?,deps=?,stale=0 WHERE id=?',(ver,encode(data),encode(deps),id))
        else:
            # A supplied ID is allowed only for an existing record or internally reserved job result.
            ver=1;self.db.execute('INSERT INTO records VALUES(?,?,?,?,?,?,0)',(id,cid,kind,ver,encode(data),encode(deps)))
        self.db.execute('INSERT INTO versions VALUES(?,?,?,?,?)',(id,ver,encode(data),encode(deps),now()))
        return {'record_id':id,'version':ver,'kind':kind}
    def events(self,cid,after=0,limit=50):
        with self.lock:
            self.case(cid)
            return [{**dict(r),'data':json.loads(r['data'])} for r in self.db.execute('SELECT * FROM events WHERE case_id=? AND seq>? ORDER BY seq LIMIT ?',(cid,after,limit))]
    def job(self,cid,id):
        with self.lock:
            r=self.db.execute('SELECT * FROM jobs WHERE id=? AND case_id=?',(id,cid)).fetchone()
            if r is None:raise NotFound('job not found in case')
            d=dict(r)
            for k in ['spec','snapshot','result']: d[k]=json.loads(d[k]) if d[k] else None
            return d
