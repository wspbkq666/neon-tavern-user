"""只报告能定位的原文字面匹配，不把引用或 AI 概括当成完整覆盖。"""
import difflib
import re
from .ai_text_filter import strip_formatting_instructions
from .context_selection import split_source


def filter_source_with_records(source):
    cleaned=strip_formatting_instructions(source)
    if cleaned==source:
        return cleaned,[]
    records=[]
    for kind,start,end,new_start,new_end in difflib.SequenceMatcher(None,source,cleaned,autojunk=True).get_opcodes():
        if kind!='equal':
            records.append({'start':start,'end':end,'original':source[start:end],'replacement':cleaned[new_start:new_end],'reason':'格式清理器移除或替换，实际设定是否误删需人工确认'})
    return cleaned,records


def strings(value,path='fields'):
    if isinstance(value,str):
        if len(value.strip())>=2:
            yield path,value
    elif isinstance(value,dict):
        for key,item in value.items():
            yield from strings(item,f'{path}.{key}')
    elif isinstance(value,list):
        for index,item in enumerate(value):
            yield from strings(item,f'{path}[{index}]')


def build_coverage_report(source,drafts,filter_records):
    references=[]
    unmatched=[]
    for index,draft in enumerate(drafts):
        for field,text in strings(draft.get('fields',{})):
            positions=[]
            position=source.find(text)
            while position>=0 and len(positions)<100:
                positions.append(position)
                position=source.find(text,position+1)
            if not positions:
                unmatched.append({'draft_index':index,'field':field,'reason':'字段内容无法按原文定位，可能是合并或改写，需要确认'})
            for position in positions:
                references.append({'draft_index':index,'field':field,'start':position,'end':position+len(text),'unique':len(positions)==1,'unknown':draft.get('type')=='unknown'})
    paragraphs=[]
    for row in split_source(source):
        refs=[ref for ref in references if ref['start']<row['end'] and ref['end']>row['start']]
        affected=[record for record in filter_records if record['start']<row['end'] and record['end']>row['start']]
        coverage=set()
        for ref in refs:
            if ref['unique'] and not ref['unknown']:
                coverage.update(range(max(row['start'],ref['start']),min(row['end'],ref['end'])))
        required={offset for offset in range(row['start'],row['end']) if not source[offset].isspace()}
        matched=bool(required) and required.issubset(coverage) and not affected
        paragraphs.append({**row,'status':'原文匹配' if matched else '待确认','references':refs,'filter_records':affected,
            'priority':'重点核对' if re.search(r'禁止|不得|不能|必须|只有|仅|如果|条件|状态|身份|能力',row['text']) else '一般核对',
            'reason':'字段逐字覆盖本段；仍需确认分类与实际含义' if matched else '本段存在未匹配内容、清理变化或无法确认的来源'})
    return {'paragraphs':paragraphs,'unmatched_fields':unmatched,'filter_records':filter_records,
        'confirmed_coverage_percent':None,'note':'原文匹配仅说明字面可定位，不代表语义完整；未确认内容不会宣称完整覆盖。'}
