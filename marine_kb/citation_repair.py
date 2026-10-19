"""Repair passage selections without changing generated facts or citations."""
import json,re
from .model_json import model_json

PROMPT=(
 '你只修复evidence_quotes段号选择，不回答问题。answer是待核对草稿，不能当事实来源。'
 '对answer每个已引用的资料，选择真正支持该句的原文passage_id；含数值时所选段必须包含对应数值、单位和上下文。'
 '可为同一citation选择多个段。不新增citation，不修改answer，不因为数字相同就选无关段。'
 '无法支持的citation列入unsupported_citations，不能强贴引用。'
 '只返回JSON：{"evidence_quotes":[{"citation":1,"passage_id":1}],"unsupported_citations":[]}。'
)


def apply_quote_refs(raw,choices,evidence,decode):
    draft=model_json(raw);choices=model_json(choices)
    if not isinstance(draft,dict) or not isinstance(choices,dict):raise ValueError('quote_repair_invalid_json')
    answer=draft.get('answer');quotes=choices.get('evidence_quotes')
    if not isinstance(answer,str) or choices.get('unsupported_citations') or not isinstance(quotes,list) or not 1<=len(quotes)<=32:
        raise ValueError('quote_repair_unsupported')
    cited={int(n) for n in re.findall(r'\[资料(\d+)\]',answer)}
    selected=[]
    for row in quotes:
        if not isinstance(row,dict) or set(row)!={'citation','passage_id'}:raise ValueError('quote_repair_invalid_selection')
        if type(row['citation']) is not int or row['citation'] not in cited or type(row['passage_id']) is not int:
            raise ValueError('quote_repair_unknown_source')
        selected.append(row)
    draft['evidence_quotes']=selected
    result=json.dumps(draft,ensure_ascii=False)
    decode(result,evidence)
    return result
