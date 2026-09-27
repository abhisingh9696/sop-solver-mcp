"""Versioned, strict input contract. All quantities use the declared bucket/UOM."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Number = Annotated[float, Field(ge=0, le=1e12, allow_inf_nan=False)]
Positive = Annotated[float, Field(gt=0, le=1e12, allow_inf_nan=False)]
Name = Annotated[str, Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_.:-]+$')]

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', validate_assignment=True)

class Resource(Strict):
    id: Name
    capacity: list[Number]

class Material(Strict):
    id: Name
    initial_inventory: Number = 0
    unit_cost: Number
    lead_time_buckets: int = Field(ge=0, le=60)
    scheduled_receipts: list[Number]
    purchase_limit: list[Number]  # indexed by release period

class Product(Strict):
    id: Name
    unit: Name
    demand: list[Number]
    history: list[Number] = Field(default_factory=list, max_length=240)
    initial_inventory: Number = 0
    initial_inventory_value: Number = 0
    initial_backlog: Number = 0
    selling_price: Number
    conversion_cost: Number
    holding_cost: Positive
    backlog_cost: Positive
    outsource_cost: Number
    outsource_limit: list[Number]
    production_limit: list[Number]
    safety_stock: list[Number]
    resources: dict[Name, Positive]  # hours per unit, or other consistent resource UOM
    bom: dict[Name, Positive] = Field(default_factory=dict)

class Finance(Strict):
    opening_cash: Number = 0
    opening_ar: Number = 0
    opening_ap: Number = 0
    ar_lag_buckets: int = Field(default=0, ge=0, le=60)
    ap_lag_buckets: int = Field(default=0, ge=0, le=60)
    fixed_opex: list[Number]
    capex: list[Number]
    depreciation: list[Number]
    financing_inflows: list[Number]
    minimum_cash: float | None = Field(default=None, allow_inf_nan=False)

class Policy(Strict):
    min_on_time_fill_rate: float = Field(default=0.95, ge=0, le=1, allow_inf_nan=False)
    require_zero_terminal_backlog: bool = True
    max_planning_cost: Number | None = None
    gate_min_confidence: float = Field(default=0.85, ge=0, le=1)
    gate_min_top_probability: float = Field(default=0.85, ge=0, le=1)
    gate_min_margin: float = Field(default=0.20, ge=0, le=1)
    max_iterations: int = Field(default=3, ge=1, le=3)
    stress_demand_multipliers: list[Positive] = Field(default_factory=lambda: [1.0], min_length=1, max_length=10)
    min_stress_fill_rate: float | None = Field(default=None, ge=0, le=1)

class PlanningInput(Strict):
    schema_version: Literal['1.0'] = '1.0'
    dataset_id: Name
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    bucket: Literal['day', 'week', 'month']
    periods: list[Name] = Field(min_length=1, max_length=60)
    products: list[Product] = Field(min_length=1, max_length=50)
    resources: list[Resource] = Field(min_length=1, max_length=20)
    materials: list[Material] = Field(default_factory=list, max_length=100)
    finance: Finance
    policy: Policy = Field(default_factory=Policy)

    @model_validator(mode='after')
    def consistency(self):
        n = len(self.periods)
        for name, ids in [('period', self.periods), ('product', [x.id for x in self.products]),
                          ('resource', [x.id for x in self.resources]), ('material', [x.id for x in self.materials])]:
            if len(set(ids)) != len(ids):
                raise ValueError(f'duplicate {name} IDs')
        for obj in [*self.products, *self.resources, *self.materials, self.finance]:
            for name, value in obj:
                if isinstance(value, list) and name != 'history' and len(value) != n:
                    raise ValueError(f'{type(obj).__name__}.{name}: requires {n} buckets')
        rid, mid = {r.id for r in self.resources}, {m.id for m in self.materials}
        for p in self.products:
            if not p.resources or not set(p.resources) <= rid or not set(p.bom) <= mid:
                raise ValueError(f'{p.id}: missing/unknown resource or BOM material')
            if p.initial_inventory == 0 and p.initial_inventory_value != 0:
                raise ValueError('inventory value requires inventory quantity')
            if p.initial_inventory and p.initial_backlog:
                raise ValueError('net simultaneous initial stock/backlog before submitting')
        if n * (len(self.products)*6 + len(self.materials)*3) > 12000:
            raise ValueError('model too large for this single-process deployment')
        return self
