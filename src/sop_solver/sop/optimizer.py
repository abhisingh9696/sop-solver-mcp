"""Sparse multi-product aggregate LP with shared resources and finite materials."""
import numpy as np
from scipy.optimize import linprog
from scipy.sparse import lil_matrix
from .schema import PlanningInput

MODES = {'economic':1.0, 'service':5.0, 'resilience':20.0}

def optimize(data: PlanningInput, mode: str = 'economic') -> dict:
    if mode not in MODES: raise ValueError('unknown candidate mode')
    n = len(data.periods); keys=[]; costs=[]; bounds=[]
    for p in data.products:
        for k, cost, limit in [('production',p.conversion_cost,p.production_limit),
                              ('outsource',p.outsource_cost,p.outsource_limit),
                              ('inventory',p.holding_cost,None),
                              ('backlog',p.backlog_cost*MODES[mode],None),
                              ('shipment',0,None)]:
            for t in range(n):
                keys.append((p.id,k,t)); costs.append(cost)
                low=p.safety_stock[t] if k=='inventory' else 0
                high=limit[t] if limit is not None else None
                if k=='backlog' and t==n-1 and data.policy.require_zero_terminal_backlog: high=0
                bounds.append((low,high))
    for m in data.materials:
        for k in ['release','inventory','receipt']:
            for t in range(n):
                keys.append((m.id,'material_'+k,t)); costs.append(m.unit_cost if k=='receipt' else 0)
                bounds.append((0,m.purchase_limit[t] if k=='release' and t+m.lead_time_buckets<n else (0 if k=='release' else None)))
    ix={k:i for i,k in enumerate(keys)}; eq=[]; ub=[]; be=[]; bu=[]
    def row(target, rhs, terms):
        (eq if target=='eq' else ub).append({ix[k]:v for k,v in terms.items()})
        (be if target=='eq' else bu).append(rhs)
    for p in data.products:
        for t in range(n):
            # FG balance: I_t = I_prev + production + outsourcing - shipments.
            terms={(p.id,'inventory',t):1,(p.id,'production',t):-1,(p.id,'outsource',t):-1,(p.id,'shipment',t):1}
            if t: terms[p.id,'inventory',t-1]=-1
            row('eq',p.initial_inventory if t==0 else 0,terms)
            terms={(p.id,'backlog',t):1,(p.id,'shipment',t):1}
            if t: terms[p.id,'backlog',t-1]=-1
            row('eq',p.demand[t]+(p.initial_backlog if t==0 else 0),terms)
        # Bucket service surrogate is conservative: total backlog-unit-periods bounded.
        row('ub',(1-data.policy.min_on_time_fill_rate)*sum(p.demand),{(p.id,'backlog',t):1 for t in range(n)})
    for r in data.resources:
        for t in range(n):
            row('ub',r.capacity[t],{(p.id,'production',t):p.resources[r.id] for p in data.products if r.id in p.resources})
    for m in data.materials:
        for t in range(n):
            terms={(m.id,'material_receipt',t):1}
            if t>=m.lead_time_buckets: terms[m.id,'material_release',t-m.lead_time_buckets]=-1
            row('eq',m.scheduled_receipts[t],terms)
            terms={(m.id,'material_inventory',t):1,(m.id,'material_receipt',t):-1}
            if t: terms[m.id,'material_inventory',t-1]=-1
            for p in data.products:
                if m.id in p.bom: terms[p.id,'production',t]=p.bom[m.id]
            row('eq',m.initial_inventory if t==0 else 0,terms)
    if data.policy.max_planning_cost is not None:
        terms={}
        for p in data.products:
            for k,cost in [('production',p.conversion_cost),('outsource',p.outsource_cost),('inventory',p.holding_cost),('backlog',p.backlog_cost)]:
                for t in range(n): terms[p.id,k,t]=cost
        for m in data.materials:
            for t in range(n): terms[m.id,'material_receipt',t]=m.unit_cost
        row('ub',data.policy.max_planning_cost,terms)
    f=data.finance
    if f.minimum_cash is not None:
        for t in range(n):
            terms={}
            cash_constant=f.opening_cash+f.opening_ar-f.opening_ap+sum(f.financing_inflows[:t+1])-sum(f.fixed_opex[:t+1])-sum(f.capex[:t+1])
            for p in data.products:
                for u in range(t+1):
                    terms[p.id,'production',u]=p.conversion_cost
                    terms[p.id,'inventory',u]=p.holding_cost
                    if u+f.ap_lag_buckets<=t: terms[p.id,'outsource',u]=p.outsource_cost
                    if u+f.ar_lag_buckets<=t: terms[p.id,'shipment',u]=-p.selling_price
            for m in data.materials:
                for u in range(t+1):
                    if u+f.ap_lag_buckets<=t: terms[m.id,'material_receipt',u]=m.unit_cost
            row('ub',cash_constant-f.minimum_cash,terms)
    def matrix(rows):
        a=lil_matrix((len(rows),len(keys)))
        for i,r in enumerate(rows):
            for j,v in r.items(): a[i,j]=v
        return a.tocsr()
    result=linprog(costs,A_ub=matrix(ub),b_ub=bu,A_eq=matrix(eq),b_eq=be,bounds=bounds,
                   method='highs',options={'time_limit':20})
    if not result.success:
        return {'status':'INFEASIBLE' if result.status==2 else 'SOLVER_FAILED','solver_status':int(result.status),
                'message':result.message,'mode':mode,'diagnosis':'No IIS certificate is claimed. Inspect capacity, material lead time, safety stock, service and terminal constraints.'}
    x=np.maximum(result.x,0)
    def series(id,k): return [float(x[ix[id,k,t]]) for t in range(n)]
    return {'status':'OPTIMAL','mode':mode,'objective':float(result.fun),'solver':'scipy/HiGHS LP',
            'products':{p.id:{k:series(p.id,k) for k in ['production','outsource','inventory','backlog','shipment']} for p in data.products},
            'materials':{m.id:{k:series(m.id,'material_'+k) for k in ['release','receipt','inventory']} for m in data.materials},
            'service_constraint':'sum of backlog-unit-periods <= (1-target) * horizon demand; actual FIFO fill is recomputed by critic',
            'quantity_policy':'continuous aggregate units; integer lots and sequencing require another solver module'}
