from typing import Annotated,Literal
from pydantic import Field
from .models import Model
Finite=Annotated[float,Field(allow_inf_nan=False)]
Vector=Annotated[list[Finite],Field(min_length=1,max_length=50000)]
class Check(Model):
    left:str
    right:str
    relation:Literal['eq','le','ge']
    tolerance:Finite=Field(default=1e-7,ge=0,le=1)
class Formula(Model):
    parameters:dict[str,Finite]
    expressions:dict[str,str]
    checks:list[Check]=Field(default_factory=list,max_length=100)
class Scenario(Model):
    name:str
    parameters:dict[str,Finite]
class Sensitivity(Formula):
    scenarios:list[Scenario]=Field(min_length=1,max_length=100)
class Descriptive(Model):values:Vector
class Cashflow(Model):
    cashflows:Vector
    discount_rate:Finite=Field(gt=-1)
class Linear(Model):
    c:Vector
    A_ub:list[list[Finite]]|None=None
    b_ub:list[Finite]|None=None
    A_eq:list[list[Finite]]|None=None
    b_eq:list[Finite]|None=None
    bounds:list[tuple[Finite|None,Finite|None]]|None=None
    integrality:list[Literal[0,1]]|None=None
class Welch(Model):
    a:Vector
    b:Vector
    alpha:Finite=Field(default=.05,gt=0,lt=1)
class OLS(Model):
    x:list[list[Finite]]
    y:Vector
class Forecast(Model):
    history:Vector
    horizon:int=Field(ge=1,le=60)
    season:int=Field(default=1,ge=1,le=60)
class SOP(Model):
    data:dict
    mode:Literal['economic','service','resilience']='economic'
class Planner(Model):
    data:dict
class Inventory(Model):
    policy:Literal['newsvendor','safety_stock','ss_policy']='newsvendor'
    data:dict
class Smoothing(Model):
    history:Vector
    horizon:int=Field(ge=1,le=60)
    alpha:Finite|None=None
    beta:Finite|None=None
    gamma:Finite|None=None
    season:int=Field(default=12,ge=2,le=60)
class Ladder(Model):
    history:Vector
    horizon:int=Field(ge=1,le=60)
    season:int=Field(default=1,ge=1,le=60)
class Adjust(Model):
    forecast:Vector
    adjustments:dict[str,Finite]
    note:str=''
class Track(Model):
    history:Vector
    forecast:Vector
class Simulate(Model):
    data:dict
    templates:list[Literal['base','demand_high','demand_low','supplier_failure','capacity_shock','price_cut']]=Field(default_factory=lambda:['base'])
class ArimaData(Model):
    history:Vector
    horizon:int=Field(ge=1,le=60)
    order:list[int]|None=None
    seasonal_order:list[int]|None=None
class Arima(Model):
    data:ArimaData
class RegressionForecastData(Model):
    history:Vector
    features:dict[str,Vector]
    future_features:dict[str,Vector]
class RegressionForecast(Model):
    data:RegressionForecastData
class MultiEchelonData(Model):
    stores:list[dict]
    warehouse:dict|None=None
    service_level:Finite=Field(gt=0,lt=1)
class MultiEchelon(Model):
    data:MultiEchelonData
class PythonJob(Model):
    code:str=Field(min_length=1,max_length=50000)
    input_data:dict
MODELS={'formula':Formula,'sensitivity':Sensitivity,'descriptive':Descriptive,'cashflow':Cashflow,
    'linear_program':Linear,'milp':Linear,'welch_ttest':Welch,'ols':OLS,'forecast':Forecast,'sop_plan':SOP,
    'plan_multi_period':Planner,'rough_cut':Planner,'mrp_run':Planner,'inventory_policy':Inventory,
    'plan_pnl':Planner,'simulate':Simulate,'holt':Smoothing,'holt_winters':Smoothing,
    'forecast_ladder':Ladder,'forecast_adjust':Adjust,'forecast_track':Track,
    'arima':Arima,'regression_forecast':RegressionForecast,'multi_echelon_inventory':MultiEchelon,
    'python_sandbox':PythonJob}
SCHEMAS={k:v.model_json_schema() for k,v in MODELS.items()}
# Expose full nested S&OP contract rather than an opaque data object.
from .sop.schema import PlanningInput
SCHEMAS['sop_plan']['sop_data_contract']=PlanningInput.model_json_schema()
def validate(method,inputs):
    if method not in MODELS:raise ValueError('UNSUPPORTED_CAPABILITY')
    return MODELS[method].model_validate(inputs).model_dump(exclude_none=True)
