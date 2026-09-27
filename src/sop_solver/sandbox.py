"""Opt-in Docker execution. No host Python exec fallback exists."""
import json,os,shutil,subprocess,uuid,tempfile,selectors,time
WRAPPER='''import json,sys,contextlib,io
p=json.load(sys.stdin)
ns={"input_data":p["input_data"]}
with contextlib.redirect_stdout(io.StringIO()):
    exec(compile(p["code"],"analysis.py","exec"),ns)
print(json.dumps(ns["result"],allow_nan=False))
'''
def available():return os.getenv('ENABLE_CUSTOM_PYTHON')=='true' and shutil.which('docker') is not None

def execute(inputs,timeout):
    if not available():raise ValueError('UNSUPPORTED_CAPABILITY: isolated Docker runner not enabled')
    code=inputs.get('code');payload=inputs.get('input_data',{})
    if not isinstance(code,str) or not 1<=len(code)<=50000:raise ValueError('invalid code length')
    data=json.dumps({'code':code,'input_data':payload}).encode()
    if len(data)>1_000_000:raise ValueError('sandbox input exceeds 1MB')
    image=os.environ.get('SANDBOX_IMAGE','case-solver-python:1');name='case-job-'+uuid.uuid4().hex
    cmd=['docker','run','--name',name,'--rm','--pull=never','--network=none','--read-only','--log-driver=none',
        '--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=64','--memory=512m','--cpus=1',
        '--user=65534:65534','--tmpfs','/tmp:rw,noexec,nosuid,size=32m','-e','OPENBLAS_NUM_THREADS=1',
        '-e','OMP_NUM_THREADS=1','-i',image,'python','-I','-c',WRAPPER]
    proc=None
    try:
        with tempfile.TemporaryFile() as inp:
            inp.write(data);inp.seek(0)
            proc=subprocess.Popen(cmd,stdin=inp,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            deadline=time.monotonic()+timeout;raw=bytearray()
            with selectors.DefaultSelector() as sel:
                sel.register(proc.stdout,selectors.EVENT_READ)
                while True:
                    left=deadline-time.monotonic()
                    if left<=0:raise TimeoutError('sandbox timeout')
                    for key,_ in sel.select(min(.2,left)):
                        chunk=os.read(key.fd,65536)
                        if not chunk:
                            proc.wait(timeout=max(.1,deadline-time.monotonic()))
                            if proc.returncode:raise ValueError('sandbox process failed')
                            return {'result':json.loads(raw),'numeric_valid':None,'validation_required':True,
                                    'warning':'Execution succeeded; result requires independent validation.'}
                        raw.extend(chunk)
                        if len(raw)>1_000_000:raise ValueError('sandbox output exceeds 1MB')
    finally:
        if proc is not None:
            proc.kill() if proc.poll() is None else None
            proc.wait(timeout=5)
            proc.stdout.close()
        subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10,check=False)
