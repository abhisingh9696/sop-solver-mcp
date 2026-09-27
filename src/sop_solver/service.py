"""Case workflow service. No hidden reasoning API: the MCP host is the analyst."""
import json, os, threading
from concurrent.futures import ThreadPoolExecutor
from pydantic import TypeAdapter
from .models import CaseInput,Record,AnalysisSpec,RouteInput,FinalInput
from .store import Store,Conflict,NotFound,encode,uid,now,hash_value
from . import engines,sources,jev
from .sandbox import available

class Service:
    def __init__(self,path):
        self.store=Store(path);self.pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='case-job')
    def close(self):self.pool.shutdown(wait=True);self.store.close()
    def capabilities(self):
        return {'mode':'host_model_led','version':'1.0.0','methods':{k:v for k,v in engines.METHODS.items() if k!='python_sandbox' or available()},
            'python_sandbox_enabled':available(),'sources':['host text','PDF text','XLSX formulas/cached values','CSV','DOCX text'],
            'limits':{'source_bytes':sources.MAX_BYTES,'pages_or_sheets':200,'local_workers':2},
            'not_implemented':['automatic host-model calls','automatic OCR/chart interpretation','built-in web research','automatic report reasoning','operational order execution'],
            'continuation':'Use case_inspect after each mutation/job completion; expected_revision is optimistic concurrency, not a confidence score.'}
    def open(self,data,request_id):return self.store.open(CaseInput.model_validate(data).model_dump(),request_id)
    def require(self,cid,ids,fresh=True):
        result=[]
        for id in set(ids):
            r=self.store.record(cid,id)
            if fresh and r['stale']:raise Conflict('STALE_DEPENDENCY: '+id)
            result.append(r)
        return result
    def inspect(self,cid):
        c=self.store.case(cid);records=self.store.records(cid);jobs=[]
        with self.store.lock:
            jobs=[dict(r) for r in self.store.db.execute('SELECT id,kind,status,created FROM jobs WHERE case_id=?',(cid,))]
        return {**c,'records':[{'id':r['id'],'kind':r['kind'],'version':r['version'],'stale':r['stale']} for r in records],
            'jobs':jobs,'completion':self.completion(cid),
            'next_action':'Read blockers, inspect required records, then submit one justified next task. The host performs interpretation and synthesis.'}
    def register_source(self,cid,revision,key,name,content_b64=None,text=None,origin='CASE'):
        if origin not in ['CASE','EXTERNAL']:raise ValueError('source origin CASE or EXTERNAL required')
        if origin=='EXTERNAL' and self.store.case(cid)['data']['research_policy']!='external_allowed':raise ValueError('case-only policy forbids external sources')
        data=sources.ingest(name,content_b64,text);data['origin']=origin
        def fn():
            for r in self.store.records(cid,'source'):
                if r['data']['sha256']==data['sha256']:return {'record_id':r['id'],'duplicate':True}
            self.store.db.execute("UPDATE cases SET state='EVIDENCE_REVIEW' WHERE id=?",(cid,))
            return self.store.put(cid,'source',data,[])
        return self.store.mutate(cid,revision,key,{'hash':data['sha256'],'name':name,'origin':origin},'source_register',fn)
    def source_inspect(self,cid,id,start=0,limit=5):
        if not 0<=start or not 1<=limit<=20:raise ValueError('invalid pagination')
        r=self.store.record(cid,id)
        if r['kind']!='source':raise ValueError('not a source')
        d=r['data'];segs=d['segments'][start:start+limit]
        # Each page is capped in response; full page can be retrieved with record inspection if needed.
        return {'source_id':id,'version':r['version'],'name':d['name'],'warnings':d['warnings'],
            'segments':segs,'total_segments':len(d['segments']),'next_start':start+limit if start+limit<len(d['segments']) else None}
    def review_source(self,cid,revision,key,id,locator,status,note,replacement_text=None):
        if status not in ['REVIEWED','EXCLUDED','NEEDS_OCR']:raise ValueError('invalid coverage status')
        if len(note.strip())<5:raise ValueError('review note required')
        def fn():
            r=self.store.record(cid,id);d=r['data']
            if r['kind']!='source':raise ValueError('not a source')
            seg=next((s for s in d['segments'] if s['locator']==locator),None)
            if seg is None:raise ValueError('unknown locator')
            if replacement_text is not None:
                if len(replacement_text)>100000:raise ValueError('replacement too large')
                seg['previous_text_hash']=hash_value(seg['text']);seg['text']=replacement_text;seg['host_corrected']=True
            if status=='REVIEWED' and len(seg['text'].strip())<1:raise ValueError('cannot review blank source without supplying extraction')
            seg['review_status']=status;seg['review_note']=note
            return self.store.put(cid,'source',d,[],id)
        return self.store.mutate(cid,revision,key,{'id':id,'locator':locator,'status':status,'note':note,'replacement_text':replacement_text},'source_review',fn)
    def record(self,cid,revision,key,payload,depends_on=None,record_id=None):
        obj=TypeAdapter(Record).validate_python(payload);d=obj.model_dump();kind=d['kind'];deps=list(depends_on or [])
        if record_id:self.store.record(cid,record_id)  # caller may update only an existing in-case ID
        def fn():
            nonlocal deps
            if kind=='evidence':
                source=self.store.record(cid,d['source_id'])
                if source['kind']!='source':raise ValueError('evidence must reference a source')
                seg=next((s for s in source['data']['segments'] if s['locator']==d['locator']),None)
                if seg is None or d['excerpt'] not in seg['text']:raise ValueError('excerpt/locator does not match source text')
                if d['label']=='CASE_FACT' and source['data']['origin']!='CASE':raise ValueError('external source cannot become case fact')
                if d['label']=='EXTERNAL_FACT' and source['data']['origin']!='EXTERNAL':raise ValueError('external label requires external source')
                deps.append(d['source_id'])
            for field in ['input_refs','support_refs','contradicting_refs','scope_refs','result_refs','resolution_refs','prerequisite_tasks']:
                deps.extend(d.get(field,[]))
            if kind in ['assessment','external_result']:
                deps.append(d['hypothesis_id'] if kind=='assessment' else d['task_id'])
            for r in self.require(cid,deps):
                if kind=='task' and r['id'] in d['prerequisite_tasks'] and r['kind']!='task':raise ValueError('prerequisite must be task')
            if kind=='external_result' and self.store.record(cid,d['task_id'])['kind']!='task':raise ValueError('external result requires task')
            if kind=='assessment':
                if self.store.record(cid,d['hypothesis_id'])['kind']!='hypothesis':raise ValueError('assessment requires hypothesis')
                for rid in d['result_refs']:
                    if self.store.record(cid,rid)['kind'] not in ['result','external_result']:raise ValueError('assessment references must be results')
            if kind=='task' and d['method'] not in [*engines.METHODS,'host_reasoning']:
                raise ValueError('UNSUPPORTED_CAPABILITY; use host_reasoning for qualitative/external analytical work')
            if kind=='unknown' and d['status']=='RESOLVED' and (not d['resolution'].strip() or not d['resolution_refs']):raise ValueError('resolved unknown needs explanation and references')
            if kind=='brief':
                old=self.store.records(cid,'brief')
                if old and record_id is None:raise ValueError('update existing brief instead of creating two')
                self.store.db.execute("UPDATE cases SET state='PLANNING' WHERE id=?",(cid,))
            if kind=='review':
                self.store.db.execute("UPDATE cases SET state='REVIEWING' WHERE id=?",(cid,))
            return self.store.put(cid,kind,d,deps,record_id)
        return self.store.mutate(cid,revision,key,{'data':d,'deps':deps,'id':record_id},'record',fn)
    def search(self,cid,query,limit=10):
        if not 1<=limit<=30:raise ValueError('limit 1–30')
        terms=query.casefold().split()
        if not terms:raise ValueError('empty query')
        hits=[]
        for r in self.store.records(cid):
            if r['kind']=='source':
                for s in r['data']['segments']:
                    score=sum(s['text'].casefold().count(t) for t in terms)
                    if score:
                        pos=min([s['text'].casefold().find(t) for t in terms if t in s['text'].casefold()])
                        hits.append({'record_id':r['id'],'locator':s['locator'],'score':score,'excerpt':s['text'][max(0,pos-200):pos+1200]})
        return {'matches':sorted(hits,key=lambda x:-x['score'])[:limit],'notice':'Lexical retrieval, not exhaustive source review. Use source_inspect for coverage.'}
    def submit(self,cid,revision,key,spec,kind='analysis'):
        d=AnalysisSpec.model_validate(spec).model_dump() if kind=='analysis' else RouteInput.model_validate(spec).model_dump()
        if kind=='analysis':
            from .method_schemas import validate
            d['inputs']=validate(d['method'],d['inputs'])
        refs=d['input_refs']+[d['task_id']] if kind=='analysis' else d['evidence_refs']
        def fn():
            c=self.store.case(cid);self.require(cid,refs)
            if kind=='analysis':
                task=self.store.record(cid,d['task_id'])
                if task['kind']!='task' or task['data']['method']!=d['method']:raise ValueError('analysis method must match task')
                if not set(task['data']['input_refs'])<=set(d['input_refs']):raise ValueError('include every task input reference')
                for tid in task['data']['prerequisite_tasks']:
                    if not self.task_done(cid,tid):raise ValueError('prerequisite task not validated')
                    # Track actual validated prerequisite outputs, not only task definitions.
                    for prior in self.store.records(cid):
                        if prior['kind'] in ['result','external_result'] and not prior['stale'] and prior['data'].get('task_id')==tid:
                            refs.append(prior['id'])
                if d['method']=='python_sandbox' and not available():raise ValueError('UNSUPPORTED_CAPABILITY: Docker runner disabled')
            else:
                allowed=set(jev.ACTIONS)|set(c['data'].get('extra_actions',[]))
                if d.get('mode','route')!='compare' and not set(d['criteria'])<=allowed:
                    raise ValueError('unknown Jev action; declare extra actions at case_open (extra_actions) or use mode=compare for alternative ranking')
                for rid in d['evidence_refs']:
                    r=self.store.record(cid,rid)
                    if r['kind']=='evidence':
                        src=self.store.record(cid,r['data']['source_id'])
                        seg=next((s for s in src['data']['segments'] if s['locator']==r['data']['locator']),None)
                        if seg and seg.get('review_status') in ['NEEDS_OCR','EXCLUDED']:
                            raise ValueError(f'evidence {rid} cites segment {seg["locator"]} with review_status {seg.get("review_status")}; review or exclude before routing Jev on it')
            count=self.store.db.execute('SELECT COUNT(*) FROM jobs WHERE case_id=? AND kind=?',(cid,kind)).fetchone()[0]
            budget=c['data']['budget']['analysis_jobs' if kind=='analysis' else 'jev_calls']
            if count>=budget:raise ValueError('BUDGET_REACHED: submit within the configured case budget')
            active=self.store.db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('QUEUED','RUNNING')").fetchone()[0]
            if active>=12:raise ValueError('job queue full')
            snapshot={r['id']:r['version'] for r in self.require(cid,refs)}
            id=uid('job');self.store.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,0,?)',(id,cid,kind,'QUEUED',encode(d),encode(snapshot),None,now()))
            self.store.db.execute("UPDATE cases SET state='ANALYZING' WHERE id=?",(cid,))
            return {'job_id':id,'status':'QUEUED'}
        out=self.store.mutate(cid,revision,key,{'kind':kind,'spec':d},'submit',fn)
        self.pool.submit(self._work,cid,out['job_id'])
        return out
    def _work(self,cid,jid):
        # Idempotent retries may enqueue the same handle: only one worker can claim it.
        with self.store.transaction():
            changed=self.store.db.execute("UPDATE jobs SET status='RUNNING' WHERE id=? AND status='QUEUED'",(jid,)).rowcount
            if not changed:return
        j=self.store.job(cid,jid);d=j['spec']
        try:
            if j['cancel']:status='CANCELLED';result=None
            else:
                if j['kind']=='analysis':result=engines.run(d['method'],d['inputs'],d['timeout_seconds'])
                else:
                    evidence=[]
                    for rid in d['evidence_refs']:
                        r=self.store.record(cid,rid,version=j['snapshot'][rid]);evidence.append({'id':rid,'kind':r['kind'],'data':r['data']})
                    if len(encode(evidence))>40000:raise ValueError('Jev evidence exceeds 40k characters; select compact evidence records')
                    result=jev.decide(d,evidence,
                        extra_actions=self.store.case(cid)['data'].get('extra_actions',[]),
                        compare=d.get('mode','route')=='compare')
                    if result['action']=='READY_FOR_SYNTHESIS' and self.completion(cid,ignore_job_id=jid)['status']=='BLOCKED':
                        result['proposed_action']=result['action'];result['action']='ESCALATE_REASONING';result['veto']='completion_blocked'
                encode(result);status='SUCCEEDED'
            with self.store.transaction():
                j=self.store.job(cid,jid)
                stale=any(self.store.record(cid,r)['version']!=v or self.store.record(cid,r)['stale'] for r,v in j['snapshot'].items())
                if j['cancel']:status='CANCELLED'
                elif stale:status='STALE'
                self.store.db.execute('UPDATE jobs SET status=?,result=? WHERE id=?',(status,encode(result) if result is not None else None,jid))
                if status=='SUCCEEDED':
                    record_kind='result' if j['kind']=='analysis' else 'decision'
                    output={'job_id':jid,'task_id':d.get('task_id'),'method':d.get('method','jev'),
                        'input_versions':j['snapshot'],'output':result,'assumptions':d.get('assumptions'),
                        'validation_plan':d.get('validation_plan'),'host_interpretation_required':True}
                    rid=self.store.put(cid,record_kind,output,list(j['snapshot']),id=('result_'+d['task_id'] if j['kind']=='analysis' else 'decision_'+jid))
                    if j['kind']!='analysis' and result and not result.get('degraded'):
                        usage=result.get('usage') or {}
                        jd={'kind':'jev_decision','job_id':jid,'question':d.get('question',''),
                            'criteria':d.get('criteria',{}),'selected':result.get('selected_option',''),
                            'confidence':float(result.get('confidence',0.0)),'probability':float(result.get('top_probability',0.0)),
                            'margin':float(result.get('margin',0.0)),'gates_passed':bool(result.get('gates_passed',False)),
                            'action_taken':str(result.get('action','')),'cost_usd':usage.get('cost')}
                        self.store.put(cid,'jev_decision',jd,list(j['snapshot']),id='jev_'+jid)
                else:rid={}
                c=self.store.case(cid);rev=c['rev']+1
                self.store.db.execute('UPDATE cases SET rev=?,updated=? WHERE id=?',(rev,now(),cid))
                self.store.db.execute('INSERT INTO events(case_id,rev,at,action,data) VALUES(?,?,?,?,?)',(cid,rev,now(),'job_finished',encode({'job_id':jid,'status':status,**rid})))
        except Exception as exc:
            with self.store.transaction():
                failure={'error':type(exc).__name__,'message':str(exc)[:500]}
                self.store.db.execute("UPDATE jobs SET status='FAILED',result=? WHERE id=?",(encode(failure),jid))
                rev=self.store.case(cid)['rev']+1
                self.store.db.execute('UPDATE cases SET rev=?,updated=? WHERE id=?',(rev,now(),cid))
                self.store.db.execute('INSERT INTO events(case_id,rev,at,action,data) VALUES(?,?,?,?,?)',(cid,rev,now(),'job_failed',encode({'job_id':jid,**failure})))
    def cancel(self,cid,revision,key,jid):
        def fn():
            self.store.job(cid,jid);self.store.db.execute('UPDATE jobs SET cancel=1 WHERE id=?',(jid,));return {'job_id':jid,'cancellation_requested':True}
        return self.store.mutate(cid,revision,key,{'job_id':jid},'cancel',fn)
    def task_done(self,cid,tid):
        results=[r for r in self.store.records(cid) if not r['stale'] and r['kind'] in ['result','external_result'] and r['data'].get('task_id')==tid]
        if not results:return False
        for r in results:
            if r['kind']=='result' and r['data']['output'].get('numeric_valid') is False:continue
            reviews=[v for v in self.store.records(cid,'review') if not v['stale'] and r['id'] in v['data']['scope_refs']]
            if any(self.review_pass(v['data']) for v in reviews):return True
        return False
    @staticmethod
    def review_pass(d):
        return not d['unresolved_material_findings'] and d['method_validity']=='PASS' and all(d[k]!='FAIL' for k in ['source_fidelity','numerical_validity','method_validity','decision_validity'])
    def completion(self,cid,ignore_job_id=None):
        self.store.case(cid);rs=self.store.records(cid);fresh=[r for r in rs if not r['stale']];block=[];conditions=[]
        by=lambda k:[r for r in fresh if r['kind']==k]
        if not by('source'):block.append('No registered case source')
        for s in by('source'):
            for seg in s['data']['segments']:
                if seg['review_status'] not in ['REVIEWED','EXCLUDED']:block.append(f"Source coverage unresolved: {s['id']} {seg['locator']}")
        if not by('brief'):block.append('No current decision brief')
        if not by('task'):block.append('No investigation tasks')
        for t in by('task'):
            if t['data']['required'] and not self.task_done(cid,t['id']):block.append('Required task lacks a fresh reviewed result: '+t['id'])
        if not by('hypothesis'):block.append('No competing hypothesis investigation registered')
        for h in by('hypothesis'):
            a=[a for a in by('assessment') if a['data']['hypothesis_id']==h['id']]
            if not a:block.append('Hypothesis has no current assessment: '+h['id'])
            elif a[-1]['data']['disposition'] in ['INCONCLUSIVE','NOT_TESTABLE_WITH_AVAILABLE_DATA']:conditions.append('Unresolved hypothesis: '+h['id'])
        for u in by('unknown'):
            if u['data']['status']=='OPEN':(block if u['data']['blocking'] else conditions).append('Open question: '+u['data']['question'])
        for a in by('assumption'):
            if a['data']['material'] and a['data']['status']=='PROPOSED':conditions.append('Unaccepted material assumption: '+a['id'])
            if a['data']['status']=='REJECTED':block.append('Rejected assumption remains in workspace: '+a['id'])
        alternatives=by('alternative')
        if len(alternatives)<2:block.append('Fewer than two current alternatives')
        for a in alternatives:
            if a['data']['feasibility']=='UNASSESSED':block.append('Unassessed alternative: '+a['id'])
            if a['data']['feasibility']=='CONDITIONAL':conditions.append('Conditional alternative: '+a['id'])
        claims=by('claim')
        if not claims:block.append('No evidence-linked recommendation claims')
        for c in claims:
            if c['data']['material'] and c['data']['review']!='SUPPORTED':block.append('Unreviewed/disputed material claim: '+c['id'])
        if self.store.case(cid)['data'].get('finance_required'):
            fin=[r for r in fresh if r['kind']=='result' and r['data'].get('method')=='plan_pnl'
                 and r['data'].get('output',{}).get('numeric_valid')]
            if not fin:block.append('Case requires financial integration (finance_required); submit a valid plan_pnl analysis before finalizing')
        reviews=by('review')
        decision_scope={r['id'] for r in fresh if r['kind'] in ['brief','alternative','claim']}
        if not any(self.review_pass(r['data']) and r['data']['decision_validity']=='PASS' and decision_scope<=set(r['data']['scope_refs']) for r in reviews):block.append('No current passing decision challenge covering brief, alternatives and claims')
        for r in reviews:
            block.extend(r['data']['unresolved_material_findings'])
        stale=[r['id'] for r in rs if r['stale'] and r['kind'] in ['brief','task','hypothesis','assessment','claim','alternative','review','external_result','result']]
        if stale:block.append('Stale analytical records require revision: '+', '.join(stale[:20]))
        with self.store.lock:
            active=self.store.db.execute("SELECT COUNT(*) FROM jobs WHERE case_id=? AND status IN ('QUEUED','RUNNING') AND id!=?",(cid,ignore_job_id or '')).fetchone()[0]
        if active:block.append('Jobs still running or queued')
        return {'status':'BLOCKED' if block else ('CONDITIONAL' if conditions else 'PASSED'),'blockers':block,'conditions':conditions,
            'notice':'Structural/evidence workflow checks. Host-authored reviews are judgments, not proof of universal correctness.'}
    def finalize(self,cid,revision,key,final):
        d=FinalInput.model_validate(final).model_dump()
        def fn():
            check=self.completion(cid)
            if check['status']=='BLOCKED':raise ValueError('COMPLETION_BLOCKED: '+'; '.join(check['blockers'][:8]))
            if check['status']=='CONDITIONAL' and not d['conditions']:raise ValueError('conditional case requires stated conditions')
            self.require(cid,d['claim_refs']+d['alternative_refs'])
            for rid in d['claim_refs']:
                if self.store.record(cid,rid)['kind']!='claim':raise ValueError('claim_refs require claims')
            for rid in d['alternative_refs']:
                if self.store.record(cid,rid)['kind']!='alternative':raise ValueError('alternative_refs require alternatives')
            deps=[r['id'] for r in self.store.records(cid) if not r['stale'] and r['kind']!='deliverable']
            status='COMPLETED_CONDITIONAL' if check['status']=='CONDITIONAL' or d['conditions'] else 'COMPLETED'
            report='# '+self.store.case(cid)['data']['title']+'\n\n'
            for title,value in [('Recommendation',d['recommendation']),('Rationale',d['rationale']),('Implementation',d['implementation']),('Decision boundaries',d['decision_boundaries']),('Conditions','\n'.join(d['conditions'])),('Limitations',d['limitations'])]:report+='## '+title+'\n\n'+value+'\n\n'
            report+='## Evidence references\n\n'+'\n'.join('- '+rid for rid in d['claim_refs']+d['alternative_refs'])+'\n'
            out=self.store.put(cid,'deliverable',{'final':d,'markdown':report,'completion':check},deps)
            self.store.db.execute('UPDATE cases SET state=? WHERE id=?',(status,cid));return {**out,'state':status}
        return self.store.mutate(cid,revision,key,d,'finalize',fn)
