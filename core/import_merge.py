"""由用户确认同名分段是否属于同一素材；保留各段原文，冲突不能静默覆盖。"""
import copy
from .smart_import import ITEM_KEYS,normalize_smart_import

TEXT_FIELDS={'personality','memories','scenario','speech_habits','description'}


def duplicate_groups(drafts):
    groups={}
    for index,draft in enumerate(drafts):
        name=draft.get('fields',{}).get('name','')
        if name and draft.get('type') in ('npc','player','worldbook'):
            groups.setdefault((draft['type'],name),[]).append(index)
    return [{'type':kind,'name':name,'indices':indices} for (kind,name),indices in groups.items() if len(indices)>1]


def preview_merge(drafts,indices,choices=None):
    choices=choices or {}
    if not isinstance(indices,list) or not 2<=len(indices)<=100 or any(type(index) is not int or not 0<=index<len(drafts) for index in indices) or len(set(indices))!=len(indices):
        raise ValueError('请选择至少两个不同的草稿')
    originals=[drafts[index] for index in sorted(indices)]
    identity={(row['type'],row['fields'].get('name')) for row in originals}
    if len(identity)!=1 or originals[0]['type'] not in ('npc','player','worldbook'):
        raise ValueError('只允许合并类型和原文名称相同的草稿')
    conflicts=[]
    def merge(values,path):
        unique=[]
        for value in values:
            if value not in unique: unique.append(copy.deepcopy(value))
        if len(unique)==1: return unique[0]
        if all(isinstance(value,dict) for value in unique):
            keys=dict.fromkeys(key for value in unique for key in value)
            return {key:merge([value[key] for value in unique if key in value],f'{path}.{key}') for key in keys}
        if all(isinstance(value,str) for value in unique) and path.split('.')[-1] in TEXT_FIELDS:
            return '\n\n'.join(value for value in unique if value)
        if all(isinstance(value,list) for value in unique) and path.split('.')[-1] not in ('categories',):
            rows=[]
            for value in unique:
                for row in value:
                    if row not in rows: rows.append(copy.deepcopy(row))
            return rows
        if path.endswith('.categories') and originals[0]['type']=='player':
            return list(dict.fromkeys(item for value in unique for item in value))
        selected=choices.get(path)
        valid=type(selected) is int and 0<=selected<len(unique)
        conflicts.append({'field':path,'values':unique,'selected':selected if valid else None})
        return copy.deepcopy(unique[selected if valid else 0])
    merged=copy.deepcopy(originals[0])
    merged['fields']=merge([row['fields'] for row in originals],'fields')
    merged['source_excerpt']='\n\n'.join(dict.fromkeys(row.get('source_excerpt','') for row in originals))[:2000]
    merged['confidence']=min(row.get('confidence',0) for row in originals)
    merged['warnings']=list(dict.fromkeys([warning for row in originals for warning in row.get('warnings',[])]+['由用户确认同名分段属于同一素材；文字设定保留各段原文，原分段结果仍保存在任务中。']))
    item={key:merged[key] for key in ITEM_KEYS}
    normalized,_=normalize_smart_import({'items':[item]})
    merged={**merged,**normalized[0]}
    return {'draft':merged,'conflicts':conflicts,'originals':originals}
