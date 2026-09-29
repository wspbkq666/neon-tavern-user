import re


_STYLE_BLOCK = re.compile(r"<style\b[^>]*>.*?</style\s*>", re.IGNORECASE | re.DOTALL)
_STYLE_ATTRIBUTE = re.compile(r"\s+style\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
_STYLE_TAG = re.compile(r"</?\s*(?:font|span|div)\b[^>]*>", re.IGNORECASE)
_STYLE_BBCODE = re.compile(r"\[/?(?:color|size|font)(?:=[^\]]*)?\]", re.IGNORECASE)
_FORMAT_HEADER = re.compile(r"(?:回答|回复|输出)(?:的)?格式(?:如下|为|是|：|:)", re.IGNORECASE)
_FORMAT_EXAMPLE = re.compile(
    r"(?:动作描写|旁白|dialogue|narration|灰色字|颜色代码|占位符|模板)|"
    r"^\s*(?:对话|台词|dialogue)\s*(?:[.。…·]{2,}|\{\{?[^}]+\}\}?)\s*$|"
    r"^\s*[`*_#>~-]+\s*$",
    re.IGNORECASE,
)
_FORMAT_PREFIX = re.compile(
    r"^\s*(?:请)?(?:用|使用|采用)\s*(?:"
    r"(?:红色|蓝色|绿色|黄色|白色|黑色|灰色|#[0-9a-f]{3,8})\s*(?:字体|文字|字)?|"
    r"(?:TXT|JSON|XML|YAML|Markdown|HTML|CSS|表格|列表|代码框|代码块|文本框|引用框)\s*(?:格式|模板|结构)?"
    r")\s*(?:来)?(?:写|输出|回复|回答|呈现|显示|排版)\s*[:：，,]?\s*",
    re.IGNORECASE,
)
_FORMAT_CLAUSE = re.compile(
    r"(?:"
    r"(?:字体|文字|字号|字体大小|字体颜色)[^。！？!?，,；;\n]{0,28}"
    r"(?:颜色|大小|字号|改成|改为|设为|设置为|调整为|变成|红色|蓝色|绿色|黄色|白色|黑色|\d+\s*(?:px|pt|em|rem))|"
    r"(?:改成|改为|设为|设置为|调整为|变成)[^。！？!?，,；;\n]{0,20}"
    r"(?:字体|字号|字体大小|字体颜色|红色字体|蓝色字体|绿色字体|加粗|粗体|斜体)|"
    r"(?:输出|回复|回答|内容)[^。！？!?，,；;\n]{0,24}"
    r"(?:框内|框里|方框|文本框|代码框|代码块|引用框|div|span)|"
    r"(?:红色|蓝色|绿色|指定的|特定的|固定的)?(?:框内|框里|方框|文本框|代码框|代码块|引用框)[^。！？!?，,；;\n]{0,20}(?:输出|回复|回答|内容)|"
    r"(?:放在|放到|放进|写在|写入|包在|包裹在|置于)[^。！？!?，,；;\n]{0,24}"
    r"(?:框内|框里|方框|文本框|代码框|代码块|引用框)|"
    r"(?:用|使用|采用)[^。！？!?，,；;\n]{0,16}(?:HTML|html标签|div标签|span标签)[^。！？!?，,；;\n]{0,12}(?:包裹|包装|输出|写入|显示)|"
    r"(?:使用|采用|改为|输出|回复|回答)[^。！？!?，,；;\n]{0,20}"
    r"(?:粗体|加粗|斜体|Markdown表格|表格格式|项目符号|代码块|引用块|标题格式)|"
    r"(?:输出|回复|回答|正文|文本|内容|每次回复)[^。！？!?，,；;\n]{0,24}"
    r"(?:TXT|JSON|XML|YAML|Markdown|HTML|代码框|代码块|表格|列表|模板|格式|结构|换行|分段|缩进|间隔)|"
    r"(?:以|按|按照)\s*(?:TXT|JSON|XML|YAML|Markdown|HTML|表格|列表|代码框|代码块)[^。！？!?，,；;\n]{0,16}"
    r"(?:格式|模板|结构|输出|回复|回答)|"
    r"(?:每行|每段|段落|换行|空行|缩进|行间距|分隔符)[^。！？!?，,；;\n]{0,20}(?:必须|要求|固定|统一|保持|使用|采用|输出|回复|回答)|"
    r"(?:用|使用|采用|改成|改为|设为|设置为|调整为)[^。！？!?，,；;\n]{0,18}"
    r"(?:红色|蓝色|绿色|黄色|白色|黑色|灰色|#[0-9a-f]{3,8})[^。！？!?，,；;\n]{0,8}(?:字体|文字|字)|"
    r"(?:font[- ]?(?:color|size|family)|text[- ]?color|font-size|color\s*:\s*#[0-9a-f]{3,8}|"
    r"(?:font|text)[- ]?(?:color|size)\s*(?:=|:|to)?\s*(?:red|blue|green|black|white|#[0-9a-f]{3,8}|\d+\s*(?:px|pt|em|rem))|"
    r"wrap[^.?!\n]{0,32}(?:box|code block|div)|(?:put|output|render)[^.?!\n]{0,32}(?:inside|in)[^.?!\n]{0,16}(?:box|code block|div)|"
    r"<style\b|style\s*=)"
    r")",
    re.IGNORECASE,
)
_CLAUSE_SEPARATOR = re.compile(r"([。！？!?，,；;\n]+)")


def strip_formatting_instructions(text):
    """Remove visual layout directives while preserving surrounding source wording."""
    if not isinstance(text, str) or not text:
        return text
    text = _STYLE_BLOCK.sub("", text)
    text = _STYLE_ATTRIBUTE.sub("", text)
    text = _STYLE_TAG.sub("", text)
    text = _STYLE_BBCODE.sub("", text)

    paragraphs = re.split(r"(\n+)", text)
    cleaned = []
    skip_format_example = False
    for part in paragraphs:
        if not part or part.isspace():
            if not skip_format_example:
                cleaned.append(part)
            continue
        if _FORMAT_HEADER.search(part):
            skip_format_example = True
            continue
        if skip_format_example:
            if _FORMAT_EXAMPLE.search(part.strip()):
                continue
            skip_format_example = False
        clauses = _CLAUSE_SEPARATOR.split(part)
        kept = []
        for index in range(0, len(clauses), 2):
            clause = _FORMAT_PREFIX.sub("", clauses[index])
            separator = clauses[index + 1] if index + 1 < len(clauses) else ""
            if not clause:
                continue
            format_match = _FORMAT_CLAUSE.search(clause)
            if format_match:
                # Remove the directive itself while retaining setting text around it.
                prefix = clause[:format_match.start()].rstrip()
                suffix = clause[format_match.end():].lstrip()
                remainder = " ".join(value for value in (prefix, suffix) if value)
                if remainder:
                    kept.extend((remainder, separator))
                continue
            kept.extend((clause, separator))
        cleaned.append("".join(kept))
    return re.sub(r"[，,；;]+(?=\s*(?:\n|$))", "", "".join(cleaned)).strip()


def sanitize_ai_value(value):
    """Recursively filter user supplied text values before they reach an AI prompt."""
    if isinstance(value, str):
        cleaned = strip_formatting_instructions(value)
        return cleaned, cleaned != value
    if isinstance(value, list):
        result = []
        changed = False
        for item in value:
            cleaned, item_changed = sanitize_ai_value(item)
            result.append(cleaned)
            changed = changed or item_changed
        return result, changed
    if isinstance(value, dict):
        result = {}
        changed = False
        for key, item in value.items():
            cleaned_key, key_changed = sanitize_ai_value(key) if isinstance(key, str) else (key, False)
            cleaned_item, item_changed = sanitize_ai_value(item)
            changed = changed or key_changed or item_changed
            if isinstance(cleaned_key, str) and cleaned_key:
                result[cleaned_key] = cleaned_item
            elif cleaned_key != key:
                changed = True
        return result, changed
    return value, False
