"""Independent recomputation of every implemented hard constraint."""
import math
from .schema import PlanningInput
from .finance import reconcile
TOL=1e-5

def validate(data: PlanningInput, plan: dict) -> dict:
    issues=[]; rates={}; n=len(data.periods)
    def check(ok, code, entity, period=None):
        if not ok: issues.append({'code':code,'entity':entity,'period':period})
    if plan.get('status')!='OPTIMAL':
        return {'feasible':False,'violations':[{'code':'NO_OPTIMAL_PLAN'}],'fill_rates':{}}
    try:
        for group, objects, fields in [('products',data.products,['production','outsource','inventory','backlog','shipment']),('materials',data.materials,['release','receipt','inventory'])]:
            if set(plan[group])!={o.id for o in objects}: raise ValueError('ID mismatch')
            for o in objects:
                for k in fields:
                    values=plan[group][o.id][k]
                    if len(values)!=n or any(not isinstance(v,(float,int)) or not math.isfinite(v) or v < -TOL for v in values):
                        raise ValueError('invalid quantity')
    except (KeyError,TypeError,ValueError):
        return {'feasible':False,'violations':[{'code':'MALFORMED_PLAN'}],'fill_rates':{}}
    for p in data.products:
        q=plan['products'][p.id]; inv=p.initial_inventory; back=p.initial_backlog; ontime=0
        for t in range(n):
            prod,ext,ship,i,b=[q[k][t] for k in ['production','outsource','shipment','inventory','backlog']]
            check(abs(i-inv-prod-ext+ship)<TOL,'FG_BALANCE',p.id,t)
            check(abs(b-back-p.demand[t]+ship)<TOL,'BACKLOG_BALANCE',p.id,t)
            check(prod<=p.production_limit[t]+TOL,'PRODUCTION_LIMIT',p.id,t)
            check(ext<=p.outsource_limit[t]+TOL,'OUTSOURCE_LIMIT',p.id,t)
            check(i+TOL>=p.safety_stock[t],'SAFETY_STOCK',p.id,t)
            ontime+=min(p.demand[t],max(0,ship-back)); inv=i; back=b
        rate=ontime/sum(p.demand) if sum(p.demand) else 1.; rates[p.id]=rate
        check(rate+TOL>=data.policy.min_on_time_fill_rate,'SERVICE',p.id)
        check(sum(q['backlog'])<=(1-data.policy.min_on_time_fill_rate)*sum(p.demand)+TOL,'BACKLOG_EXPOSURE',p.id)
        check(not data.policy.require_zero_terminal_backlog or back<TOL,'TERMINAL_BACKLOG',p.id)
    capacity=[]
    for r in data.resources:
        used=[sum(plan['products'][p.id]['production'][t]*p.resources.get(r.id,0) for p in data.products) for t in range(n)]
        for t,v in enumerate(used): check(v<=r.capacity[t]+TOL,'RESOURCE_CAPACITY',r.id,t)
        capacity.append({'resource':r.id,'used':used,'available':r.capacity,
                         'utilization':[u/c if c else (0 if u<TOL else None) for u,c in zip(used,r.capacity)]})
    for m in data.materials:
        q=plan['materials'][m.id]; stock=m.initial_inventory
        for t in range(n):
            receipt=m.scheduled_receipts[t]+(q['release'][t-m.lead_time_buckets] if t>=m.lead_time_buckets else 0)
            used=sum(plan['products'][p.id]['production'][t]*p.bom.get(m.id,0) for p in data.products)
            check(abs(q['receipt'][t]-receipt)<TOL,'MATERIAL_LEAD_TIME',m.id,t)
            check(q['release'][t]<=m.purchase_limit[t]+TOL,'PURCHASE_LIMIT',m.id,t)
            check(t+m.lead_time_buckets<n or q['release'][t]<TOL,'POST_HORIZON_RELEASE',m.id,t)
            stock+=receipt-used
            check(abs(stock-q['inventory'][t])<TOL and stock>=-TOL,'MATERIAL_BALANCE',m.id,t)
    financial=reconcile(data,plan)
    check(all(abs(r['cash_reconciliation_residual'])<TOL for r in financial['rows']),'FINANCIAL_TIE_OUT','finance')
    if data.policy.max_planning_cost is not None:
        check(financial['planning_cost']<=data.policy.max_planning_cost+TOL,'COST_BUDGET','finance')
    if data.finance.minimum_cash is not None:
        check(financial['minimum_closing_cash']+TOL>=data.finance.minimum_cash,'CASH_FLOOR','finance')
    return {'feasible':not issues,'violations':issues,'fill_rates':rates,'capacity':capacity,'financials':financial}
