"""Management P&L and cash reconciliation; backlog penalties are not cash costs."""
from .schema import PlanningInput

def reconcile(data: PlanningInput, plan: dict) -> dict:
    n=len(data.periods); f=data.finance; revenue=[0.]*n; cogs=[0.]*n; conv=[0.]*n
    purchase=[0.]*n; hold=[0.]*n; penalty=[0.]*n; fg=[0.]*n; rm=[0.]*n
    mc={m.id:m.unit_cost for m in data.materials}
    for p in data.products:
        q=plan['products'][p.id]; value=p.initial_inventory_value; quantity=p.initial_inventory
        unit_material=sum(mc[k]*v for k,v in p.bom.items())
        for t in range(n):
            made=q['production'][t]; ext=q['outsource'][t]; sent=q['shipment'][t]
            value += made*(p.conversion_cost+unit_material)+ext*p.outsource_cost
            quantity += made+ext
            unit=value/quantity if quantity>1e-9 else 0
            cogs[t] += sent*unit; value-=sent*unit; quantity-=sent
            fg[t]+=value; revenue[t]+=sent*p.selling_price
            conv[t]+=made*p.conversion_cost; purchase[t]+=ext*p.outsource_cost
            hold[t]+=q['inventory'][t]*p.holding_cost; penalty[t]+=q['backlog'][t]*p.backlog_cost
    for m in data.materials:
        q=plan['materials'][m.id]
        for t in range(n):
            purchase[t]+=q['receipt'][t]*m.unit_cost
            rm[t]+=q['inventory'][t]*m.unit_cost
    ar=f.opening_ar; ap=f.opening_ap; cash=f.opening_cash; rows=[]
    initial_inv=sum(p.initial_inventory_value for p in data.products)+sum(m.initial_inventory*m.unit_cost for m in data.materials)
    prev_wc=initial_inv+ar-ap
    for t in range(n):
        receipts=(f.opening_ar if t==0 else 0)+(revenue[t-f.ar_lag_buckets] if t>=f.ar_lag_buckets else 0)
        supplier_pay=(f.opening_ap if t==0 else 0)+(purchase[t-f.ap_lag_buckets] if t>=f.ap_lag_buckets else 0)
        ar+=revenue[t]-receipts; ap+=purchase[t]-supplier_pay
        opex=f.fixed_opex[t]+hold[t]; ebitda=revenue[t]-cogs[t]-opex
        operating_cash=receipts-supplier_pay-conv[t]-opex
        cash+=operating_cash-f.capex[t]+f.financing_inflows[t]
        wc=fg[t]+rm[t]+ar-ap
        residual=operating_cash-(ebitda-(wc-prev_wc)); prev_wc=wc
        rows.append({'period':data.periods[t],'revenue':revenue[t],'cogs':cogs[t],
            'gross_profit':revenue[t]-cogs[t],'opex':opex,'ebitda':ebitda,
            'depreciation':f.depreciation[t],'ebit':ebitda-f.depreciation[t],
            'customer_receipts':receipts,'supplier_payments':supplier_pay,'conversion_payments':conv[t],
            'operating_cash_flow':operating_cash,'capex':f.capex[t],
            'financing_inflows':f.financing_inflows[t],'closing_cash':cash,
            'accounts_receivable':ar,'accounts_payable':ap,'fg_inventory_value':fg[t],
            'raw_material_inventory_value':rm[t],'working_capital':wc,
            'cash_reconciliation_residual':residual,'planning_backlog_penalty':penalty[t]})
    days={'day':1,'week':7,'month':30}[data.bucket]*n
    total_revenue=sum(revenue); total_cogs=sum(cogs); total_purchases=sum(purchase)
    dio=sum(r['fg_inventory_value']+r['raw_material_inventory_value'] for r in rows)/n/total_cogs*days if total_cogs else None
    dso=sum(r['accounts_receivable'] for r in rows)/n/total_revenue*days if total_revenue else None
    dpo=sum(r['accounts_payable'] for r in rows)/n/total_purchases*days if total_purchases else None
    return {'rows':rows,'currency':data.currency,'revenue':total_revenue,'cogs':total_cogs,
        'ebitda':sum(r['ebitda'] for r in rows),'ebit':sum(r['ebit'] for r in rows),
        'planning_cost':sum(conv)+sum(purchase)+sum(hold)+sum(penalty),
        'minimum_closing_cash':min(r['closing_cash'] for r in rows),
        'dio_days':dio,'dso_days':dso,'dpo_days':dpo,
        'ccc_days':dio+dso-dpo if all(v is not None for v in [dio,dso,dpo]) else None,
        'basis':'Perpetual weighted-average FG, standard RM cost; no tax/interest/FX. Month=30 days for indicative CCC. Opening AR/AP settle in bucket 1. Conversion paid immediately. Penalty excluded from EBITDA/cash.'}
