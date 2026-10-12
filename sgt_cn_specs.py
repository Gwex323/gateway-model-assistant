"""Chinese-provider official specifications for the observed SGT catalog.

All matching is exact (GLM case normalization only). No provider key is accepted.
"""
import json
import re
from urllib.parse import parse_qs, urlsplit
from official_limits import SpecHTML

VOLC_URL = 'https://www.volcengine.com/api/doc/getDocDetail?LibraryID=82379&DocumentID=1330310'
VOLC_PAGE = 'https://www.volcengine.com/docs/82379/1330310'
MIMO_URL = 'https://mimo.mi.com/static/docs/quick-start/summary/model.md'
KIMI_URL = 'https://platform.kimi.com/docs/guide/troubleshooting.md'
HY_URL = 'https://api.hunyuan.tencent.com/api/new-portal/hy-config'
HOSTS = {'help.aliyun.com', 'docs.bigmodel.cn', 'www.volcengine.com', 'mimo.mi.com',
         'platform.kimi.com', 'api.hunyuan.tencent.com'}


def route(model_id):
    if re.fullmatch(r'qwen\d+(?:\.\d+)?-(?:max|flash)', model_id):
        return ('cn', model_id, 'https://help.aliyun.com/zh/model-studio/' + model_id.replace('.', '-'))
    if re.fullmatch(r'glm-\d+(?:\.\d+)?(?:-flash)?', model_id, re.I):
        name = model_id.lower()
        kind = 'vlm' if name.endswith('-flash') else 'text'
        return ('cn', name, 'https://docs.bigmodel.cn/cn/guide/models/' + kind + '/' + name)
    if re.fullmatch(r'mimo-v\d+(?:\.\d+)?(?:-pro)?', model_id):
        return ('cn', model_id, MIMO_URL)
    if model_id in ('kimi-k3', 'kimi-k2.7-code', 'kimi-k2.6'):
        return ('cn', model_id, KIMI_URL)
    if model_id.startswith('doubao-seed-') or re.fullmatch(r'deepseek-v4-(?:pro|flash)-ga-\d{6}', model_id):
        return ('cn', model_id, VOLC_URL)
    if model_id == 'hy4-preview':
        return ('cn', model_id, HY_URL)
    return None


def source_url(url):
    return {VOLC_URL: VOLC_PAGE, HY_URL: 'https://hunyuan.tencent.com/model/hy-model'}.get(url, url)


def flat_html(text):
    page = SpecHTML()
    page.feed(text)
    return page, ' '.join(' '.join(page.text).split())


def clean(text):
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    return re.sub(r'[*`]', '', flat_html(text)[1]).strip()


def unique(values):
    values = set(values)
    return next(iter(values)) if len(values) == 1 else None


def parse_qwen(text, model_id):
    from provider_specs import ModelSpec, number
    page, flat = flat_html(text)
    # The model's own detail page includes the exact model ID, not just family text.
    if not re.search(r'模型调用 ID（\s*model 参数取值）：\s*' + re.escape(model_id) + r'(?![\w.-])', flat):
        return None
    pairs = []
    for table in page.tables:
        rows = {}
        for row in table:
            if len(row) not in (2, 4):
                continue
            for index in range(0, len(row), 2):
                key, value = row[index:index + 2]
                if key in rows and rows[key] != value:
                    return None
                rows[key] = value
        if '最大输入长度' not in rows or '最大输出长度' not in rows:
            continue
        input_max, output_max = number(rows['最大输入长度']), number(rows['最大输出长度'])
        context = number(rows.get('上下文长度', ''))
        thinking = number(rows.get('最大输入长度（思考模式下）', ''))
        if input_max and output_max:
            note = '输入、输出为各自上限，不保证同时用满。'
            if thinking:
                note += f'思考模式最大输入为 {thinking}。'
            pairs.append(ModelSpec(input_max, output_max, context_limit=context,
                                   safe_input_limit=thinking, note=note))
    return unique(pairs)


def parse_glm(text, model_id):
    from provider_specs import ModelSpec, number
    _, flat = flat_html(text)
    # Code examples and Model Code are identity evidence; a navigation label is not.
    ids = re.findall(r'(?:Model Code\*?\*?\s*[：:]\s*`|model\s*=\s*["\x27])([a-z0-9.-]+)', text, re.I)
    if model_id not in [x.lower() for x in ids]:
        # HTML representation of the model code / JSON example.
        if not re.search(r'(?:Model Code\s*[：:]|model\s*[=:])\s*["\x27]?'+re.escape(model_id)+r'(?![\w.-])', flat, re.I):
            return None
    matches = re.findall(r'支持\s*([\d.]+[KM])\s*上下文窗口，最大输出\s*Tokens\s*为\s*([\d.]+[KM])', flat, re.I)
    context_values, output_values = [], []
    for context, output in matches:
        context_values.append(number(context)); output_values.append(number(output))
    for label, destination in (('上下文窗口', context_values), ('最大输出', output_values)):
        pattern = r'<Card\s+title="'+label+r'"[\s\S]*?/>\s*}>\s*([\d.]+[KM])\s*</Card>'
        destination.extend(number(x) for x in re.findall(pattern, text, re.I))
    context, output = unique(context_values), unique(output_values)
    if context and output and context > output:
        return ModelSpec(context, output, True, True)
    return None


def parse_mimo(text, model_id):
    from provider_specs import ModelSpec, number
    page, _ = flat_html(text)
    found = []
    for table in page.tables:
        for row in table:
            if len(row) < 3 or clean(row[0]) != model_id:
                continue
            match = re.fullmatch(r'Context Window:\s*([\d.]+[KM]?)\s+Maximum Output:\s*([\d.]+[KM]?)', row[2], re.I)
            if match:
                context, output = (number(x) for x in match.groups())
                if context and output and context > output:
                    found.append(ModelSpec(context, output, True, True))
    return unique(found)


def parse_volc(text, model_id):
    from provider_specs import ModelSpec, number
    data = json.loads(text).get('Result') or {}
    if data.get('DocumentID') != 1330310 or data.get('LibraryID') != 82379:
        return None
    found = []
    for line in data.get('MDContent', '').splitlines():
        if not line.startswith('|'):
            continue
        row = line.strip('|').split('|')
        if len(row) != 4:
            continue
        # Only the model column may establish identity; links in other columns cannot.
        names = re.findall(r'\[([^\]]+)\]\(', row[0])
        if model_id not in names:
            continue
        limits = clean(row[2])
        values = []
        for pattern in (r'上下文窗口:\s*([\d.]+[kKmM]?)', r'最大输入:\s*([\d.]+[kKmM]?)',
                        r'最大回答(?:\([^)]*\))?:\s*([\d.]+[kKmM]?)'):
            match = re.search(pattern, limits)
            values.append(number(match[1]) if match else None)
        context, input_max, output = values
        if all(values):
            found.append(ModelSpec(input_max, output, conservative_units=True, context_limit=context,
                                   note='官方分别声明输入/回答上限；回答与输入共用总窗口，默认回答不是最大回答。'))
    return unique(found)


def parse_kimi(text, model_id):
    from provider_specs import ModelSpec
    # Read only the official output-length FAQ, never a code example/default.
    section = re.search(r'<Accordion title="Kimi 大模型的输出长度是多少">(.*?)</Accordion>', text, re.S)
    if not section:
        return None
    found = []
    for line in section[1].splitlines():
        if model_id not in re.findall(r'`(kimi-[a-z0-9.-]+)`', line):
            continue
        match = re.search(r'最大输出长度是\s*`(\d+)\*(\d+)\s*-\s*prompt_tokens`', line)
        if not match:
            continue
        context = int(match[1]) * int(match[2])
        if not 1 < context <= 100_000_000:
            continue
        default = re.search(r'默认为\s*(\d+)', line)
        found.append(ModelSpec(context, context, context_limit=context, dynamic_output=True,
                               default_output=int(default[1]) if default else None,
                               note='最大输出随本次输入变化：总上下文减去 prompt_tokens；并非固定输出上限。'))
    return unique(found)


def parse_hunyuan(text, model_id):
    from provider_specs import ModelSpec, number
    data = json.loads(text)
    if data.get('code') != 0:
        return None
    found = []
    def walk(value):
        if isinstance(value, dict):
            link = value.get('tryLink', '')
            if parse_qs(urlsplit(link).query).get('model') == [model_id]:
                match = re.search(r'([\d.]+[KM])\s*上下文', value.get('modelDesc', ''), re.I)
                if match and number(match[1]):
                    found.append(ModelSpec(number(match[1]), None, True, True,
                                           note='官方模型页仅取得上下文；尚未取得可验证的最大输出值，不猜填。'))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(data.get('data', {}).get('hyModelConfig', {}))
    return unique(found)


def parse(text, model_id, url):
    if url == VOLC_URL:
        return parse_volc(text, model_id)
    if url == MIMO_URL:
        return parse_mimo(text, model_id)
    if url == KIMI_URL:
        return parse_kimi(text, model_id)
    if url == HY_URL:
        return parse_hunyuan(text, model_id)
    host = urlsplit(url).hostname
    if host == 'help.aliyun.com':
        return parse_qwen(text, model_id)
    if host == 'docs.bigmodel.cn':
        return parse_glm(text, model_id)
    return None
