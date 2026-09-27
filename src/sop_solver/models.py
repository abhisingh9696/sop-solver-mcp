"""Typed case records. The host supplies reasoning; records preserve its evidence."""
from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, model_validator
Text=Annotated[str,Field(min_length=1,max_length=20000)]
Short=Annotated[str,Field(min_length=1,max_length=200)]
class Model(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
class Budget(Model):
    analysis_jobs:int=Field(default=40,ge=1,le=500)
    jev_calls:int=Field(default=0,ge=0,le=200)
class CaseInput(Model):
    title:Short
    objective:Text
    decision_date:str|None=None
    research_policy:Literal['case_only','external_allowed']='case_only'
    deliverables:list[Short]=Field(min_length=1,max_length=10)
    budget:Budget=Field(default_factory=Budget)
    extra_actions:list[Short]=Field(default_factory=list,max_length=10)
    finance_required:bool=False
class Evidence(Model):
    kind:Literal['evidence']='evidence'
    label:Literal['CASE_FACT','EXTERNAL_FACT','DERIVED_RESULT','INTERPRETATION']
    statement:Text
    source_id:Short
    locator:Short
    excerpt:Text
    units:str='not_applicable'
    quality_notes:Text
class Assumption(Model):
    kind:Literal['assumption']='assumption'
    statement:Text
    rationale:Text
    bounds:dict|None=None
    material:bool=True
    status:Literal['PROPOSED','ACCEPTED_FOR_ANALYSIS','REJECTED']='PROPOSED'
class Unknown(Model):
    kind:Literal['unknown']='unknown'
    question:Text
    impact:Text
    blocking:bool=True
    status:Literal['OPEN','RESOLVED']='OPEN'
    resolution:str=''
    resolution_refs:list[str]=Field(default_factory=list)
class Brief(Model):
    kind:Literal['brief']='brief'
    decision_maker:Short
    decision:Text
    options:list[Text]=Field(min_length=2,max_length=20)
    objectives:list[Text]=Field(min_length=1,max_length=20)
    constraints:list[Text]
    horizon:Text
    evaluation_criteria:list[Text]=Field(min_length=1)
class Hypothesis(Model):
    kind:Literal['hypothesis']='hypothesis'
    claim:Text
    question:Text
    competing_explanations:list[Text]=Field(min_length=1)
    prediction:Text
    disconfirmation:Text
    method:Text
    evidence_required:list[Text]=Field(min_length=1)
    evaluation_rule:Text
    identification_limits:Text
    decision_relevance:Text
class Task(Model):
    kind:Literal['task']='task'
    question:Text
    method:Short
    input_refs:list[str]=Field(default_factory=list)
    prerequisite_tasks:list[str]=Field(default_factory=list)
    acceptance_conditions:list[Text]=Field(min_length=1)
    required:bool=True
    independence_notes:str='Host-led analysis; no independent model assumed.'
class Assessment(Model):
    kind:Literal['assessment']='assessment'
    hypothesis_id:Short
    result_refs:list[str]=Field(min_length=1)
    disposition:Literal['SUPPORTED','CONTRADICTED','INCONCLUSIVE','NOT_TESTABLE_WITH_AVAILABLE_DATA']
    justification:Text
    limitations:Text
    decision_consequence:Text
class ExternalCitation(Model):
    source:Short
    passage_id:Short
    quote:Text
class Claim(Model):
    kind:Literal['claim']='claim'
    statement:Text
    claim_type:Literal['FACT','DERIVED','INTERPRETATION']
    material:bool=True
    support_refs:list[str]=Field(min_length=1)
    contradicting_refs:list[str]=Field(default_factory=list)
    review:Literal['UNREVIEWED','SUPPORTED','DISPUTED']='UNREVIEWED'
    external_citations:list[ExternalCitation]=Field(default_factory=list,max_length=20)
class Alternative(Model):
    kind:Literal['alternative']='alternative'
    name:Short
    description:Text
    result_refs:list[str]=Field(default_factory=list)
    feasibility:Literal['FEASIBLE','INFEASIBLE','CONDITIONAL','UNASSESSED']
    consequences:Text
    risks:Text
class Review(Model):
    kind:Literal['review']='review'
    scope_refs:list[str]=Field(min_length=1)
    reviewer:Short
    independence:Text
    source_fidelity:Literal['PASS','FAIL','NOT_APPLICABLE']
    numerical_validity:Literal['PASS','FAIL','NOT_APPLICABLE']
    method_validity:Literal['PASS','FAIL','NOT_APPLICABLE']
    decision_validity:Literal['PASS','FAIL','NOT_APPLICABLE']
    findings:list[Text]
    unresolved_material_findings:list[Text]
    tests_performed:Text
class ExternalResult(Model):
    kind:Literal['external_result']='external_result'
    task_id:Short
    method:Text
    findings:Text
    data:dict
    input_refs:list[str]=Field(min_length=1)
    provenance:Text
    limitations:Text
    validation_status:Literal['UNVERIFIED']='UNVERIFIED'
class JevDecision(Model):
    kind:Literal['jev_decision']='jev_decision'
    job_id:Short
    question:Text
    criteria:dict[str,Text]
    selected:str
    confidence:float
    probability:float
    margin:float
    gates_passed:bool
    action_taken:str
    cost_usd:float|None=None
Record=Annotated[Union[Evidence,Assumption,Unknown,Brief,Hypothesis,Task,Assessment,Claim,Alternative,Review,ExternalResult,JevDecision],Field(discriminator='kind')]
class Mutation(Model):
    expected_revision:int=Field(ge=0)
    request_id:Short
class AnalysisSpec(Model):
    task_id:Short
    method:Literal['descriptive','formula','sensitivity','cashflow','linear_program','milp','welch_ttest','ols','forecast','sop_plan','plan_multi_period','rough_cut','mrp_run','inventory_policy','plan_pnl','simulate','holt','holt_winters','forecast_ladder','forecast_adjust','forecast_track','arima','regression_forecast','multi_echelon_inventory','python_sandbox']
    inputs:dict
    input_refs:list[str]=Field(min_length=1,max_length=100)
    assumptions:Text
    validation_plan:Text
    timeout_seconds:int=Field(default=30,ge=1,le=90)
class RouteInput(Model):
    question:Text
    criteria:dict[str,Text]=Field(min_length=2,max_length=8)
    evidence_refs:list[str]=Field(min_length=1,max_length=20)
    mode:Literal['route','compare']='route'
    min_confidence:float=Field(default=.85,ge=.6,le=1)
    min_probability:float=Field(default=.85,ge=.6,le=1)
    min_margin:float=Field(default=.2,ge=.2,le=1)
class FinalInput(Model):
    recommendation:Text
    rationale:Text
    claim_refs:list[str]=Field(min_length=1)
    alternative_refs:list[str]=Field(min_length=2)
    implementation:Text
    decision_boundaries:Text
    conditions:list[Text]=Field(default_factory=list)
    limitations:Text
