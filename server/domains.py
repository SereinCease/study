"""Trusted declarative learning templates; never executable plugins."""
from typing import Literal
from pydantic import BaseModel,Field
from . import db

class DomainPackage(BaseModel):
    id:str=Field(pattern=r'^[a-z0-9-]+$')
    version:int=Field(ge=1)
    title:str
    knowledge_types:list[str]
    exercise_types:list[str]
    evaluation:list[Literal['ai_estimate','execution_and_rubric','verified_answer']]
    skills:list[Literal['decompose','explain','review']]
    tools:list[Literal['retrieval','isolated_runner']]
    description:str=''
    requires_scope_version:bool=False
    sample_content:bool=False


def packages():
    automation=DomainPackage.model_validate_json((db.ROOT/'samples/domain.json').read_text()).model_dump()
    objective=['single_choice','multiple_choice','true_false','fill_blank']
    common={'version':1,'knowledge_types':['concept','procedure','fact'],'skills':['decompose','explain','review'],'tools':['retrieval']}
    values=[automation,
        dict(common,id='general',title='通用知识与兴趣',exercise_types=['recall','application','short_answer'],evaluation=['ai_estimate'],description='收藏、探索与兴趣学习；不强制目标或练习。'),
        dict(common,id='postgraduate',title='考研学习',exercise_types=objective+['short_answer','essay'],evaluation=['verified_answer','ai_estimate'],description='用户导入资料与已核对题目；不内置或声称提供最新考纲。',requires_scope_version=True),
        dict(common,id='civil-service',title='考公学习',exercise_types=objective+['short_answer','essay'],evaluation=['verified_answer','ai_estimate'],description='行测客观题与申论维度反馈；成绩为学习评价，不预测录取。',requires_scope_version=True)]
    return [DomainPackage.model_validate(x).model_dump() for x in values]


def get(id):return next((p for p in packages() if p['id']==id),None)
