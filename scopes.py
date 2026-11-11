"""Reviewed applicability from section headings / clause subjects.

Special-subject clauses require that subject in the question. This conservative
gate prevents a general restaurant question inheriting a group-catering rule.
It does not assert that the sources cover all legal scopes or exceptions.
"""
import re

RULES=[
 ('A1:3',r'纺织|服装|服饰|成衣','纺织服装产品'),
 ('A3:2',r'服装|床上用品|毛巾|皮革|纺织','REACH 纺织品及皮革相关物品'),
 ('A4:1',r'供应商|工厂|面料|纱线|原料|生产','服装多层级供应链'),
]

def excluded_reason(cid,question):
    for prefix,pattern,scope in RULES:
        if (cid==prefix or cid.startswith(prefix+'.')) and not re.search(pattern,question):
            return '问题未指定该条款的特殊适用对象：'+scope
    return None
