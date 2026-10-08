"""保存原文偏移并采用保守估算选择上下文；必要内容绝不静默截断。"""
import json
import re


def estimate_tokens(value):
    text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
    return (len(text.encode('utf-8'))+1)//2+16


def split_source(text):
    if not isinstance(text,str):
        raise ValueError('原文必须是文字')
    result=[]
    start=0
    for match in re.finditer(r'\n\s*\n|$',text):
        end=match.end()
        while start<end:
            boundary=min(start+1500,end)
            result.append({'id':f'{start}:{boundary}','start':start,'end':boundary,'text':text[start:boundary]})
            start=boundary
    return result


def select_context(core_text,required_entries,optional_segments,recent_text,budget):
    if type(budget) is not int or budget<1:
        raise ValueError('上下文预算无效')
    used=estimate_tokens(core_text)+sum(estimate_tokens(item.get('content',item.get('text',''))) for item in required_entries)
    if used>budget:
        raise ValueError(f'必要设定超过上下文预算：估算需要 {used}，可用 {budget}。请调整容量或确认核心设定。')
    selected=[]
    excluded=[]
    folded=recent_text.casefold()
    for item in optional_segments:
        keys=item.get('keywords',[])
        if keys and not any(key.casefold() in folded for key in keys):
            excluded.append({'id':item['id'],'reason':'关键词未匹配'})
            continue
        cost=estimate_tokens(item)
        if used+cost>budget:
            excluded.append({'id':item['id'],'reason':'剩余预算不足'})
            continue
        selected.append(item)
        used+=cost
    return {'selected':selected,'excluded':excluded,'used_estimate':used,'budget':budget,'estimated':True}


def validate_context_policy(policy):
    if not isinstance(policy,dict) or set(policy)-{'confirmed','core_text','segments'}:
        raise ValueError('核心设定配置格式无效')
    if type(policy.get('confirmed',False)) is not bool or not isinstance(policy.get('core_text',''),str) or len(policy.get('core_text',''))>100000:
        raise ValueError('核心设定内容无效')
    entries=policy.get('segments',{})
    if not isinstance(entries,dict) or len(entries)>5000:
        raise ValueError('设定段落配置无效')
    for key,item in entries.items():
        if not isinstance(key,str) or not isinstance(item,dict) or set(item)-{'keywords','required','label'}:
            raise ValueError('设定段落配置无效')
        if type(item.get('required',False)) is not bool or not isinstance(item.get('label',''),str) or len(item.get('label',''))>200:
            raise ValueError('设定段落标签无效')
        keys=item.get('keywords',[])
        if not isinstance(keys,list) or len(keys)>200 or any(not isinstance(key,str) or not 1<=len(key)<=200 for key in keys):
            raise ValueError('设定段落关键词无效')


def character_segments(actor):
    result=[]
    policy=actor.context_policy.get('segments',{})
    for field in ('personality','memories'):
        for row in split_source(getattr(actor,field)):
            identifier=f'{field}:{row["id"]}'
            result.append({**row,'id':identifier,'field':field,**policy.get(identifier,{})})
    return result
