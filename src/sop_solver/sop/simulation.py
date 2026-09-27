"""Replay a fixed plan under explicitly supplied demand stress factors."""
from .schema import PlanningInput

def simulate(data: PlanningInput, plan: dict) -> dict:
    scenarios=[]
    for multiplier in data.policy.stress_demand_multipliers:
        rows=[]
        for p in data.products:
            inv=p.initial_inventory; backlog=p.initial_backlog; ontime=0; shipped=0; total=0
            for t,d in enumerate(p.demand):
                demand=d*multiplier; total+=demand
                available=inv+plan['products'][p.id]['production'][t]+plan['products'][p.id]['outsource'][t]
                ship=min(available,backlog+demand)
                ontime+=min(demand,max(0,ship-backlog)); shipped+=ship
                inv=available-ship; backlog=backlog+demand-ship
            rows.append({'product':p.id,'on_time_fill_rate':ontime/total if total else 1.,
                'closing_inventory':inv,'closing_backlog':backlog,'revenue':shipped*p.selling_price})
        scenarios.append({'demand_multiplier':multiplier,'products':rows})
    worst=min(x['on_time_fill_rate'] for s in scenarios for x in s['products'])
    threshold=data.policy.min_stress_fill_rate
    return {'scenarios':scenarios,'worst_fill_rate':worst,'risk_pass':threshold is None or worst+1e-5>=threshold,
            'method':'fixed-plan demand stress replay, no probabilistic likelihood or adaptive replenishment assumed',
            'warning':'Material/supply shocks require new input and reoptimization. No Monte Carlo/VaR claim.'}
