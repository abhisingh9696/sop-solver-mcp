"""Numerical tools with declared assumptions and reproducible outputs."""
import ast, math, operator, json
import numpy as np
from scipy.optimize import linprog, milp, Bounds, LinearConstraint
from scipy import stats
METHODS={
 'descriptive':'Finite numeric observations -> count, mean, spread, quantiles.',
 'formula':'Named scalar inputs plus ordered arithmetic expressions; no Python execution.',
 'sensitivity':'Explicit parameter scenarios evaluated using the same formula model.',
 'cashflow':'Explicit cash flows at equal periods and discount rate; NPV and funding exposure.',
 'linear_program':'User-formulated continuous LP with independently computed residuals and duals.',
 'milp':'User-formulated mixed integer linear model; reports solver bounds/gap and residuals.',
 'welch_ttest':'Two independent samples; Welch comparison, effect estimate and interval.',
 'ols':'Linear least squares with intercept, rank check and descriptive fit diagnostics.',
 'forecast':'Rolling-origin baseline selection; empirical bands, no calibrated-coverage claim.',
 'sop_plan':'Single-echelon shared-resource and finite-material planning module.',
 'plan_multi_period':'Multi-period produce/carry/backlog LP with shared resources, overtime capacity and chase/level strategy shaping.',
 'rough_cut':'Load-vs-capacity screen for a proposed production plan; flags overloaded periods.',
 'mrp_run':'BOM explosion with net requirements and lot sizing (lot-for-lot, fixed, max stock).',
 'inventory_policy':'Newsvendor, safety stock / reorder point and (s,S) policy replay.',
 'plan_pnl':'One-set-of-numbers finance view (P&L, cash, DSO/DIO/CCC) for a validated S&OP plan.',
 'simulate':'Template shock scenarios (demand, supplier, capacity, price) with re-optimization.',
 'holt':'Holt double exponential smoothing with fitted or supplied alpha/beta.',
 'holt_winters':'Holt-Winters seasonal smoothing (additive decomposition) with fitted or supplied constants.',
 'forecast_ladder':'Runs baselines plus SMA/WMA/EMA/SES/Holt/Holt-Winters and selects by rolling-origin MAE.',
 'forecast_adjust':'Judgmental per-period multiplier overlay on a statistical forecast.',
 'forecast_track':'Forecast-vs-actual diagnostics: bias, MAPE, RMSE, MASE, tracking signal.',
 'arima':'ARIMA/SARIMA via statsmodels with supplied orders and residual-based bands; no auto-search.',
 'regression_forecast':'Least-squares demand forecast on host-supplied exogenous features (association, not causation).',
 'multi_echelon_inventory':'Two-echelon safety-stock pooling: warehouse + stores vs decentralized sizing.',
 'python_sandbox':'Opt-in Docker-isolated Python job; disabled unless operator provisions runner.'}

def array(v,ndim=1,max_size=50000):
    a=np.asarray(v,dtype=float)
    if a.ndim!=ndim or not a.size or a.size>max_size or not np.isfinite(a).all():raise ValueError('invalid finite array/dimensions/size')
    return a

def scalar(v):
    if type(v) not in (float,int) or not math.isfinite(v) or abs(v)>1e100:raise ValueError('invalid finite scalar')
    return float(v)

OPS={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv,ast.Pow:operator.pow}
FUNCS={'abs':abs,'min':min,'max':max,'sqrt':math.sqrt,'log':math.log,'exp':math.exp}
def evaluate(expression,names):
    if not isinstance(expression,str) or len(expression)>2000:raise ValueError('expression too long')
    tree=ast.parse(expression,mode='eval')
    if len(list(ast.walk(tree)))>100:raise ValueError('expression too complex')
    def visit(n):
        if isinstance(n,ast.Constant):return scalar(n.value)
        if isinstance(n,ast.Name) and n.id in names:return scalar(names[n.id])
        if isinstance(n,ast.UnaryOp) and isinstance(n.op,(ast.USub,ast.UAdd)):
            v=visit(n.operand);return -v if isinstance(n.op,ast.USub) else v
        if isinstance(n,ast.BinOp) and type(n.op) in OPS:
            a,b=visit(n.left),visit(n.right)
            if isinstance(n.op,ast.Pow) and abs(b)>100:raise ValueError('exponent too large')
            return scalar(OPS[type(n.op)](a,b))
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id in FUNCS and not n.keywords:
            if not 1<=len(n.args)<=20:raise ValueError('invalid function arguments')
            return scalar(FUNCS[n.func.id](*[visit(x) for x in n.args]))
        raise ValueError('unsupported expression node; attributes/imports/comprehensions forbidden')
    return scalar(visit(tree.body))

def formula(i):
    if not isinstance(i.get('parameters'),dict) or not isinstance(i.get('expressions'),dict):raise ValueError('parameters and expressions objects required')
    if len(i['parameters'])>100 or not 1<=len(i['expressions'])<=100:raise ValueError('formula size limit')
    names={k:scalar(v) for k,v in i['parameters'].items()}; out={}
    for k,e in i['expressions'].items():
        if not k.isidentifier() or k in names or k in FUNCS:raise ValueError('invalid/duplicate output name')
        out[k]=evaluate(e,names);names[k]=out[k]
    checks=[]
    for check in i.get('checks',[]):
        left=evaluate(check['left'],names);right=evaluate(check['right'],names)
        tol=scalar(check.get('tolerance',1e-7))
        if not 0<=tol<=1:raise ValueError('tolerance outside allowed range')
        passed={'eq':abs(left-right)<=tol,'le':left<=right+tol,'ge':left+tol>=right}.get(check['relation'])
        if passed is None:raise ValueError('check relation must be eq, le, ge')
        checks.append({'left':left,'right':right,'relation':check['relation'],'passed':passed})
    return {'outputs':out,'checks':checks,'numeric_valid':all(c['passed'] for c in checks),
            'validation_basis':'Expression execution and supplied checks; host must assess formulation and units.'}

def linear(i,integer=False,timeout=30):
    c=array(i['c']);n=len(c)
    if n>1000:raise ValueError('at most 1000 variables')
    rows=[];lbs=[];ubs=[]
    for ak,bk,is_eq in [('A_ub','b_ub',False),('A_eq','b_eq',True)]:
        if ak in i:
            a=array(i[ak],2,max_size=200000);b=array(i[bk])
            if a.shape!=(len(b),n):raise ValueError('constraint dimensions mismatch')
            rows.extend(a.tolist());lbs.extend(b.tolist() if is_eq else [-np.inf]*len(b));ubs.extend(b.tolist())
    bounds=i.get('bounds',[[0,None] for _ in range(n)])
    if len(bounds)!=n:raise ValueError('bounds length mismatch')
    lo=[];hi=[]
    for pair in bounds:
        if len(pair)!=2:raise ValueError('two bounds required')
        l=-np.inf if pair[0] is None else scalar(pair[0]);h=np.inf if pair[1] is None else scalar(pair[1])
        if l>h:raise ValueError('lower exceeds upper')
        lo.append(l);hi.append(h)
    integrality=i.get('integrality',[0]*n)
    if len(integrality)!=n or any(type(v)!=int or v not in [0,1] for v in integrality):raise ValueError('integrality must be 0/1 per variable')
    if integer:
        con=LinearConstraint(np.asarray(rows),lbs,ubs) if rows else None
        r=milp(c,integrality=integrality,bounds=Bounds(lo,hi),constraints=con,options={'time_limit':timeout})
    else:
        if any(integrality):raise ValueError('integer variables require milp')
        r=linprog(c,A_ub=i.get('A_ub'),b_ub=i.get('b_ub'),A_eq=i.get('A_eq'),b_eq=i.get('b_eq'),bounds=bounds,method='highs',options={'time_limit':timeout})
    if not r.success:return {'status':'INFEASIBLE' if r.status==2 else 'NOT_OPTIMAL','solver_status':int(r.status),'message':str(r.message),'numeric_valid':False}
    x=np.asarray(r.x);viol=[]
    for row,l,h in zip(rows,lbs,ubs):
        v=float(np.dot(row,x));viol.append(max(0,l-v,v-h))
    viol.extend(max(0,l-v,v-h) for l,v,h in zip(lo,x,hi))
    integ=max([abs(v-round(v)) for v,k in zip(x,integrality) if k]+[0.])
    residual=max(viol+[0.]); objective=float(c@x)
    duals={}
    if not integer and getattr(r,'ineqlin',None) is not None and getattr(r.ineqlin,'marginals',None) is not None:
        duals['constraint_shadow_prices']=[float(v) for v in r.ineqlin.marginals]
        duals['variable_reduced_costs']=[float(v) for v in (r.lower.marginals if getattr(r.lower,'marginals',None) is not None else [0.0]*n)]
    return {'status':'OPTIMAL','x':x.tolist(),'objective':objective,'max_constraint_violation':residual,
            'max_integer_violation':integ,'numeric_valid':residual<1e-6 and integ<1e-6,
            'solver_gap':float(r.mip_gap) if getattr(r,'mip_gap',None) is not None else None,
            **({'duals':duals} if duals else {}),
            'warning':'Optimality applies to supplied formulation. Variable units and business assumptions require review.'}

def run(method,i,timeout=30):
    if method=='formula':return formula(i)
    if method=='sensitivity':
        scenarios=i['scenarios']
        if not isinstance(scenarios,list) or not 1<=len(scenarios)<=100:raise ValueError('1–100 explicit scenarios required')
        rows=[]
        for s in scenarios:
            params={**i['parameters'],**s['parameters']}
            rows.append({'name':s['name'],**formula({'parameters':params,'expressions':i['expressions'],'checks':i.get('checks',[])})})
        return {'scenarios':rows,'numeric_valid':all(r['numeric_valid'] for r in rows),'warning':'Scenarios have no assumed probabilities.'}
    if method=='descriptive':
        a=array(i['values'])
        return {'count':len(a),'sum':float(a.sum()),'mean':float(a.mean()),'median':float(np.median(a)),
                'sample_std':float(a.std(ddof=1)) if len(a)>1 else None,'min':float(a.min()),'max':float(a.max()),'numeric_valid':True}
    if method=='cashflow':
        cf=array(i['cashflows']);rate=scalar(i['discount_rate'])
        if rate<=-1:raise ValueError('discount rate must exceed -1')
        if len(cf)>1000:raise ValueError('horizon too large')
        t=np.arange(len(cf));discount=cf/((1+rate)**t);cum=cf.cumsum()
        if not np.isfinite(discount).all():raise ValueError('discounting overflow; revise rate/horizon')
        crossings=np.flatnonzero(cum>=0)
        payback=int(crossings[0]) if len(crossings) and cf[0]<0 else None
        return {'npv':float(discount.sum()),'discounted_cashflows':discount.tolist(),'cumulative_cash':cum.tolist(),
            'peak_cumulative_funding':float(max(0,-cum.min())),'first_nonnegative_cash_bucket':payback,
            'numeric_valid':True,'basis':'First cashflow is t=0; equal periods; no taxes/terminal value invented. Crossing is discrete, not interpolated payback.'}
    if method in ['linear_program','milp']:return linear(i,method=='milp',timeout)
    if method=='welch_ttest':
        a=array(i['a']);b=array(i['b']);alpha=scalar(i.get('alpha',.05))
        if len(a)<2 or len(b)<2 or not 0<alpha<1:raise ValueError('need >=2 observations/sample and valid alpha')
        va=a.var(ddof=1)/len(a);vb=b.var(ddof=1)/len(b)
        if va+vb==0:raise ValueError('zero variance; inferential test not defined here')
        se=math.sqrt(va+vb);df=(va+vb)**2/(va**2/(len(a)-1)+vb**2/(len(b)-1));diff=float(a.mean()-b.mean())
        stat=diff/se;p=float(2*stats.t.sf(abs(stat),df));crit=float(stats.t.ppf(1-alpha/2,df))
        return {'mean_difference':diff,'t':stat,'degrees_freedom':float(df),'p_value_two_sided':p,
            'confidence_interval':[diff-crit*se,diff+crit*se],'alpha':alpha,'numeric_valid':True,
            'warning':'Requires independent observations and defensible sampling. No causal claim or multiple-testing correction is implied.'}
    if method=='ols':
        x=array(i['x'],2);y=array(i['y']);x=np.column_stack([np.ones(len(x)),x])
        if len(x)!=len(y) or len(y)<=x.shape[1]:raise ValueError('insufficient observations/dimension mismatch')
        beta,_,rank,_=np.linalg.lstsq(x,y,rcond=None)
        if rank!=x.shape[1]:raise ValueError('rank deficient design')
        pred=x@beta;res=y-pred;sse=float(res@res);sst=float(((y-y.mean())**2).sum())
        return {'coefficients_with_intercept':beta.tolist(),'predictions':pred.tolist(),'residuals':res.tolist(),
            'r_squared':1-sse/sst if sst else None,'max_normal_equation_residual':float(np.abs(x.T@res).max()),
            'numeric_valid':True,'warning':'In-sample association only. No causal identification or predictive holdout evaluation.'}
    if method=='forecast':
        from .sop.forecast import forecast
        return {**forecast(i['history'],i['horizon'],i.get('season',1)),'numeric_valid':True}
    if method=='sop_plan':
        from .sop.schema import PlanningInput
        from .sop.optimizer import optimize
        from .sop.critic import validate
        from .sop.simulation import simulate
        d=PlanningInput.model_validate(i['data']);p=optimize(d,i.get('mode','economic'));v=validate(d,p)
        return {'plan':p,'validation':v,'simulation':simulate(d,p) if v['feasible'] else None,'numeric_valid':v['feasible']}
    if method=='plan_multi_period':
        from .sop.planner import plan_multi_period
        return plan_multi_period(i['data'])
    if method=='rough_cut':
        from .sop.planner import rough_cut
        return rough_cut(i['data'])
    if method=='mrp_run':
        from .sop.mrp import mrp_run
        return mrp_run(i['data'])
    if method=='inventory_policy':
        from .sop.inventory import newsvendor, safety_stock, ss_policy_simulate
        kind=i.get('policy','newsvendor')
        if kind=='newsvendor':return newsvendor(i['data'])
        if kind=='safety_stock':return safety_stock(i['data'])
        if kind=='ss_policy':return ss_policy_simulate(i['data'])
        raise ValueError("policy must be newsvendor, safety_stock or ss_policy")
    if method=='plan_pnl':
        from .sop.pnl import plan_pnl
        return plan_pnl(i['data'])
    if method=='simulate':
        from .sop.pnl import simulate
        return simulate(i)
    if method=='holt':
        from .sop.forecast import holt
        return {**holt(i['history'],i['horizon'],i.get('alpha'),i.get('beta')),'numeric_valid':True}
    if method=='holt_winters':
        from .sop.forecast import holt_winters
        return {**holt_winters(i['history'],i['horizon'],i.get('season',12),i.get('alpha'),i.get('beta'),i.get('gamma')),'numeric_valid':True}
    if method=='forecast_ladder':
        from .sop.forecast import ladder
        return {**ladder(i['history'],i['horizon'],i.get('season',1)),'numeric_valid':True}
    if method=='forecast_adjust':
        from .sop.forecast import adjust
        return adjust(i['forecast'],i['adjustments'],i.get('note',''))
    if method=='forecast_track':
        from .sop.forecast import track
        return track(i['history'],i['forecast'])
    if method=='arima':
        from .sop.enterprise import arima
        return {**arima(i['data']),'numeric_valid':True}
    if method=='regression_forecast':
        from .sop.enterprise import regression_forecast
        return regression_forecast(i['data'])
    if method=='multi_echelon_inventory':
        from .sop.enterprise import multi_echelon_inventory
        return multi_echelon_inventory(i['data'])
    if method=='python_sandbox':
        from .sandbox import execute
        return execute(i,timeout)
    raise ValueError('UNSUPPORTED_CAPABILITY')
