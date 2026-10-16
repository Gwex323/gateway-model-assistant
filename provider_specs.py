"""Official model-card fallback for SGT, explicitly requested by the user.

No gateway key enters this module. IDs are never fuzzily matched to another model.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
import json
import re
import urllib.parse
import urllib.request

from official_limits import SpecHTML, fetch_deepseek_specs, PRICING_URL
import sgt_cn_specs

MAX_BYTES = 4 * 1024 * 1024
MAX_LIMIT = 100_000_000
MAX_DOCUMENTS = 64
HOSTS = {'developers.openai.com', 'platform.claude.com', 'ai.google.dev'} | sgt_cn_specs.HOSTS


@dataclass(frozen=True)
class ModelSpec:
    input_limit: int
    output_limit: int | None
    shared_context: bool = False
    conservative_units: bool = False
    context_limit: int | None = None
    safe_input_limit: int | None = None
    dynamic_output: bool = False
    default_output: int | None = None
    note: str = ''
    supports_images: bool | None = None


def valid_document_url(url):
    try:
        p = urllib.parse.urlsplit(url)
        return (p.scheme == 'https' and p.hostname in HOSTS and p.port in (None, 443)
                and not p.username and not p.password)
    except ValueError:
        return False


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if (not valid_document_url(newurl)
                or urllib.parse.urlsplit(req.full_url).hostname != urllib.parse.urlsplit(newurl).hostname):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_document(url):
    if not valid_document_url(url):
        raise ValueError('Untrusted official document URL')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SameOriginRedirect())
    # Claude serves its public Markdown representation via content negotiation.
    accept = 'text/markdown' if urllib.parse.urlsplit(url).hostname in ('platform.claude.com', 'docs.bigmodel.cn', 'platform.kimi.com') else 'text/html,application/json'
    headers = {'Accept': accept, 'User-Agent': 'GatewayModelAssistant/1.0.2'}
    body = None
    if url == sgt_cn_specs.HY_URL:
        # This is the public model page's READ-ONLY configuration query.
        body = json.dumps({'keys': ['hyModelConfig']}).encode('utf-8')
        headers['Content-Type'] = 'application/json'
        headers['accept-language'] = 'zh'
    req = urllib.request.Request(url, data=body, headers=headers)
    with opener.open(req, timeout=8) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('Official document too large')
    return data.decode('utf-8')


def number(text):
    match = re.fullmatch(r'([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*([KM]?)', text.strip(), re.I)
    if not match:
        return None
    from decimal import Decimal
    value = Decimal(match[1].replace(',', '')) * {'': 1, 'K': 1000, 'M': 1000000}[match[2].upper()]
    return int(value) if value == int(value) and 0 < value <= MAX_LIMIT else None


class ModelCardHTML(SpecHTML):
    def __init__(self):
        super().__init__()
        self.canonical = []

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        values = dict(attrs)
        if tag == 'link' and values.get('rel') == 'canonical':
            self.canonical.append(values.get('href', '').rstrip('/'))


def parse_openai(document, model_id, url):
    page = ModelCardHTML()
    page.feed(document)
    if page.canonical != [url.rstrip('/')]:
        return None
    text = ' '.join(' '.join(page.text).split())
    # The model card states exact integers, not navigation labels or prices.
    pairs = re.findall(r'(?<![\w.,])([\d,]+) context window ([\d,]+) max output tokens\b', text)
    if len(set(pairs)) != 1:
        return None
    context, output = (number(x) for x in pairs[0])
    images = True if re.search(r'\bImage\s+Input(?: only)?\b', text, re.I) else None
    return ModelSpec(context, output, True, supports_images=images) if context and output and context > output else None


def parse_gemini(document, model_id, url):
    page = SpecHTML()
    page.feed(document)
    found = []
    for table in page.tables:
        # Model code and token limits must belong to the SAME table.
        if not any(len(row) == 2 and row[0].endswith('Model code') and row[1] == model_id for row in table):
            continue
        for row in table:
            if len(row) != 2 or not row[0].endswith('Token limits'):
                continue
            m = re.fullmatch(r'Input token limit ([\d,]+) Output token limit ([\d,]+)', row[1])
            if m:
                values = [number(x) for x in m.groups()]
                if all(values):
                    image_input = any(len(r) == 2 and r[0].strip().lower() in ('inputs', 'input', 'input data types', 'supported inputs')
                                      and re.search(r'\bimages?\b', r[1], re.I) for r in table)
                    found.append(ModelSpec(*values, supports_images=True if image_input else None))
    return found[0] if found and len(set(found)) == 1 else None


def markdown_label(text):
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    return text.strip().strip('*').strip()


def parse_claude(document, model_id, url):
    rows = []
    for line in document.splitlines():
        if line.strip().startswith('|'):
            cells = [markdown_label(x) for x in line.strip().strip('|').split('|')]
            if len(cells) == 2:
                rows.append(cells)
    ids = set()
    for label, value in rows:
        if label in ('Claude API', 'Claude API alias'):
            ids.update(re.findall(r'`(claude-[a-z0-9-]+)`', value))
    if model_id not in ids:
        return None
    values = {}
    for label in ('Context window', 'Max output'):
        entries = [value for key, value in rows if key == label]
        if not entries or len(set(entries)) != 1:
            return None
        match = re.fullmatch(r'([\d,.]+\s*[KM]?) tokens', entries[0], re.I)
        if not match:
            return None
        values[label] = number(match[1])
    context, output = values['Context window'], values['Max output']
    # K/M not expanded by the source: use decimal lower bounds, label as budget.
    if context and output and context > output:
        return ModelSpec(context, output, True, any(re.search(r'[KM]', v) for k, v in rows if k in values))
    return None


def model_document(model_id):
    cn = sgt_cn_specs.route(model_id)
    if cn:
        return cn
    # Explicit provider namespaces only; never strip arbitrary SGT suffixes.
    for namespace, prefixes in {'openai/': ('gpt-', 'o1', 'o3', 'o4'),
                                 'anthropic/': ('claude-',), 'google/': ('gemini-',),
                                 'deepseek/': ('deepseek-',)}.items():
        if model_id.startswith(namespace) and model_id[len(namespace):].startswith(prefixes):
            model_id = model_id[len(namespace):]
            break
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,119}', model_id):
        return None
    if model_id.startswith('deepseek-'):
        return ('deepseek', model_id, PRICING_URL)
    if model_id.startswith(('gpt-', 'o1', 'o3', 'o4')):
        return ('openai', model_id, 'https://developers.openai.com/api/docs/models/' + model_id)
    if model_id.startswith('gemini-'):
        return ('gemini', model_id, 'https://ai.google.dev/gemini-api/docs/models/' + model_id)
    match = re.fullmatch(r'claude-((?:opus|sonnet|haiku|fable)-\d+(?:-\d{1,2})?)(?:-\d{8})?', model_id)
    if match:
        return ('claude', model_id, 'https://platform.claude.com/docs/en/models/' + match[1] + '/overview')
    return None


def enrich_sgt_catalog(catalog):
    missing = [m for m in catalog if m.max_input_tokens is None or m.max_output_tokens is None]
    plans = {m.id: model_document(m.id) for m in missing}
    # Deduplicate requests for aliases and dated IDs on one page; bounded concurrency.
    urls = list(dict.fromkeys(plan[2] for plan in plans.values() if plan))[:MAX_DOCUMENTS]

    def fetch(url):
        try:
            return fetch_deepseek_specs() if url == PRICING_URL else read_document(url)
        except (OSError, ValueError, UnicodeError):
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        docs = dict(zip(urls, pool.map(fetch, urls)))
    result = []
    parsers = {'openai': parse_openai, 'gemini': parse_gemini, 'claude': parse_claude, 'cn': sgt_cn_specs.parse}
    for model in catalog:
        from core import media_hint, image_video_hint, API_ONLY_IMPORT_HINT
        if media_hint(model.id) and model.supports_chat is not True:
            hint = API_ONLY_IMPORT_HINT if image_video_hint(model.id) else '不适用'
            result.append(replace(model, input_display=hint, output_display=hint,
                                  limit_source='媒体/嵌入模型，非文本 Token 输出',
                                  limit_note='图片、视频等输出不能换算为文本最大输出 Token；不自动写入虚构窗口值。'))
            continue
        if model.id not in plans:
            result.append(model)
            continue
        plan = plans[model.id]
        spec = None
        if plan:
            provider, official_id, url = plan
            document = docs.get(url)
            if document is not None:
                try:
                    if provider == 'deepseek':
                        raw = document.get(official_id)
                        spec = ModelSpec(*raw, shared_context=True, conservative_units=True) if raw else None
                    else:
                        spec = parsers[provider](document, official_id, url)
                except (ValueError, TypeError, UnicodeError, AttributeError, RecursionError):
                    spec = None
        if spec is None:
            status = '官方规格未获取或未匹配' if plan else '未识别官方模型 ID，未补值'
            if plan and plan[2] not in docs:
                status = '本次官方查询数量达上限，未补值'
            result.append(replace(model, limit_source=status))
            continue
        output = model.max_output_tokens if model.max_output_tokens is not None else spec.output_limit
        context = spec.input_limit if spec.shared_context else spec.context_limit
        note = spec.note
        if output is not None and context and output >= context and model.max_output_tokens is None:
            # Full-window output is a theoretical/dynamic ceiling, not a usable
            # input+output pair. Prefer the published default, otherwise split.
            output = spec.default_output or context // 2
            note += '导入使用官方默认输出或半窗口预算，避免输入/输出同时占满上下文。'
        input_limit = spec.safe_input_limit or spec.input_limit
        if context and output is not None:
            input_limit = min(input_limit, context - output)
        if ((output is not None and spec.output_limit is not None and output > spec.output_limit)
                or input_limit <= 0 or model.max_input_tokens is not None and model.max_input_tokens > input_limit):
            result.append(replace(model, limit_source='接口与官方规格冲突，未补值'))
            continue
        label = '官方规格（按 SGT 同官方）'
        if context:
            label += '·保守输入预算'
        if spec.conservative_units:
            label += '·单位取保守值'
        result.append(replace(model,
            max_input_tokens=model.max_input_tokens if model.max_input_tokens is not None else input_limit,
            max_output_tokens=output, limit_source=label, limit_source_url=sgt_cn_specs.source_url(plan[2]),
            supports_images=model.supports_images if model.supports_images is not None else spec.supports_images,
            input_display=str(model.max_input_tokens) if model.max_input_tokens is not None else
                ('≤' if spec.shared_context or spec.dynamic_output else '') + str(spec.input_limit),
            output_display=str(model.max_output_tokens) if model.max_output_tokens is not None else
                (f'{spec.output_limit}－本次输入' if spec.dynamic_output else
                 str(spec.output_limit) if spec.output_limit is not None else '未取得官方值'),
            limit_note=note + ('K/M 按保守十进制下界换算，不宣称为精确展开值。' if spec.conservative_units else '') +
                ('输入列是共享上下文上界，不是可独立用满的最大输入。' if spec.shared_context else '')))
    return result
