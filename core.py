"""Gateway discovery and conservative WorkBuddy models.json adapter (stdlib only)."""
from __future__ import annotations

import base64
from model_capabilities import image_input_support, chat_metadata_support, chat_model_support
import copy
import ctypes
import ipaddress
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

MAX_BYTES = 4 * 1024 * 1024
MAX_TOKEN_LIMIT = 100_000_000
MAX_MODELS = 10_000
ANTHROPIC_VERSION = '2023-06-01'


class UserError(Exception):
    """A message safe to display: never include response bodies or credentials."""


def valid_id(value):
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= 256
            and value == value.strip() and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def _normalize_url(value, endpoint_suffixes, allow_private_http=False):
    try:
        raw = value.strip()
        if any(c.isspace() or ord(c) < 32 for c in raw):
            raise ValueError()
        u = urllib.parse.urlsplit(raw)
        port = u.port
        if (u.scheme not in ('http', 'https') or not u.hostname or u.username is not None
                or u.password is not None or u.query or u.fragment):
            raise ValueError()
        loopback = u.hostname.lower() == 'localhost'
        private_network = False
        try:
            address = ipaddress.ip_address(u.hostname)
            loopback = loopback or address.is_loopback
            private_network = (address.is_private and not address.is_loopback
                               and not address.is_unspecified and not address.is_multicast)
        except ValueError:
            pass
        if u.scheme == 'http' and private_network and not allow_private_http:
            raise UserError('该连接使用私有网络明文 HTTP；需要明确确认风险后才能使用。')
        if u.scheme == 'http' and not loopback and not (allow_private_http and private_network):
            raise UserError('公网网关必须使用 HTTPS；HTTP 仅允许本机回环地址。')
        path = u.path.rstrip('/')
        for suffix in endpoint_suffixes:
            if path.endswith(suffix):
                path = path[:-len(suffix)]
                break
        path = path.rstrip('/') or '/v1'
        host = u.hostname.lower()
        if ':' in host:
            host = '[' + host + ']'
        if port and not (u.scheme == 'https' and port == 443 or u.scheme == 'http' and port == 80):
            host += ':' + str(port)
        return urllib.parse.urlunsplit((u.scheme, host, path, '', ''))
    except (ValueError, AttributeError):
        raise UserError('请填写有效的网关地址；不要在地址中包含账号、密钥、查询参数或片段。') from None


def normalize_url(value, allow_private_http=False):
    url = _normalize_url(
        value, ('/chat/completions', '/models'),
        allow_private_http=allow_private_http,
    )
    return _provider_root(url, 'openai')


def normalize_anthropic_url(value, allow_private_http=False):
    """Normalize an Anthropic-compatible root such as ``.../v1``."""
    url = _normalize_url(
        value, ('/messages', '/models'),
        allow_private_http=allow_private_http,
    )
    return _provider_root(url, 'anthropic')


def _is_volc_coding(url):
    parsed = urllib.parse.urlsplit(url)
    return (parsed.hostname == 'ark.cn-beijing.volces.com'
            and parsed.port in (None, 443)
            and parsed.path in ('/api/coding', '/api/coding/v1', '/api/coding/v3'))


def _provider_root(url, protocol):
    # Keep explicit versioned paths. Never substitute a pay-as-you-go root.
    if _is_volc_coding(url) and urllib.parse.urlsplit(url).path == '/api/coding':
        return url + ('/v1' if protocol == 'anthropic' else '/v3')
    if protocol == 'anthropic' and urllib.parse.urlsplit(url).path.endswith(
        ('/api/claudecode', '/api/anthropic', '/apps/anthropic', '/api/coding',
         '/claudecode', '/anthropic', '/coding', '/claude')
    ):
        return url + '/v1'
    return url


@dataclass(frozen=True)
class GatewayConnection:
    protocol: str
    base_url: str
    endpoint: str
    models_urls: tuple[str, ...]
    auth: str
    provider_name: str
    models_protocol: str = ''
    models_auth: str = ''

    def headers(self, api_key, *, models=False):
        key = check_key(api_key)
        auth = (self.models_auth or self.auth) if models else self.auth
        protocol = (self.models_protocol or self.protocol) if models else self.protocol
        headers = {'Accept': 'application/json', 'User-Agent': 'GatewayModelAssistant/1.0.3'}
        if auth == 'bearer':
            headers['Authorization'] = 'Bearer ' + key
        else:
            headers['x-api-key'] = key
        if protocol == 'anthropic':
            headers['anthropic-version'] = ANTHROPIC_VERSION
        return headers


def resolve_connection(base_url, protocol='openai', auth='auto', allow_private_http=False):
    if protocol not in ('openai', 'anthropic') or auth not in ('auto', 'bearer', 'x-api-key'):
        raise UserError('不支持的接口协议或鉴权方式。')
    normalize = normalize_anthropic_url if protocol == 'anthropic' else normalize_url
    url = normalize(base_url, allow_private_http=allow_private_http)
    volc = _is_volc_coding(url)
    if auth == 'auto':
        auth = 'bearer' if protocol == 'openai' or volc else 'x-api-key'
    path = urllib.parse.urlsplit(url).path
    segment = path.rsplit('/', 1)[-1]
    versioned = segment.startswith('v') and segment[1:].isdigit()
    candidates = [url + '/models']
    if not versioned:
        compat = ('/api/claudecode', '/api/anthropic', '/apps/anthropic',
                  '/api/coding', '/claudecode', '/anthropic', '/coding', '/claude')
        if path.endswith(compat):
            candidates.insert(0, url + '/v1/models')
        else:
            candidates.append(url + '/v1/models')
    # Discovery fallbacks never change the endpoint used by the client.
    endpoint = url + ('/messages' if protocol == 'anthropic' else '/chat/completions')
    parsed = urllib.parse.urlsplit(url)
    if (protocol == 'anthropic' and parsed.scheme == 'https'
            and parsed.hostname == 'api.deepseek.com' and parsed.port in (None, 443)
            and parsed.path == '/anthropic/v1'):
        return GatewayConnection(protocol, url, endpoint, ('https://api.deepseek.com/models',),
                                 auth, 'DeepSeek', 'openai', 'bearer')
    return GatewayConnection(protocol, url, endpoint, tuple(candidates), auth,
                             '火山 Coding Plan' if volc else '自定义网关')


def is_private_http_url(value):
    try:
        normalized = normalize_url(value, allow_private_http=True)
        parsed = urllib.parse.urlsplit(normalized)
        address = ipaddress.ip_address(parsed.hostname)
        return (parsed.scheme == 'http' and address.is_private and not address.is_loopback
                and not address.is_unspecified and not address.is_multicast)
    except (UserError, ValueError, AttributeError):
        return False


def same_gateway_url(left, right):
    """Compare gateway roots while accepting stored full endpoint URLs."""
    try:
        return (normalize_url(left, allow_private_http=True)
                == normalize_url(right, allow_private_http=True))
    except UserError:
        return False


def check_key(key):
    if not isinstance(key, str) or not key.strip() or any(ord(c) < 32 or ord(c) > 126 for c in key):
        raise UserError('请填写有效 API Key（不能包含换行或非 ASCII 字符）。')
    return key.strip()


def parse_token_limits(max_input=None, max_output=None):
    """Parse optional UI token limits into WorkBuddy's canonical field names."""
    def parse(value, label):
        if value is None or isinstance(value, str) and not value.strip():
            return None
        if isinstance(value, bool):
            raise UserError(f'{label}必须是正整数。')
        if isinstance(value, int):
            number = value
        elif isinstance(value, str) and value.strip().isdigit():
            number = int(value.strip())
        else:
            raise UserError(f'{label}必须是正整数。')
        if number < 1 or number > MAX_TOKEN_LIMIT:
            raise UserError(f'{label}必须在 1 到 {MAX_TOKEN_LIMIT} 之间。')
        return number

    result = {}
    input_value = parse(max_input, '最大输入 Token')
    output_value = parse(max_output, '最大输出 Token')
    if input_value is not None:
        result['maxInputTokens'] = input_value
    if output_value is not None:
        result['maxOutputTokens'] = output_value
    return result


def validate_token_limits(values):
    if values is None:
        return {}
    if not isinstance(values, dict) or any(
        key not in ('maxInputTokens', 'maxOutputTokens') for key in values
    ):
        raise UserError('上下文窗口设置不合法。')
    return parse_token_limits(values.get('maxInputTokens'), values.get('maxOutputTokens'))


def validate_model_token_limits(values):
    """Validate optional per-model token limits returned by model discovery."""
    if values is None:
        return {}
    if not isinstance(values, dict):
        raise UserError('逐模型上下文窗口设置不合法。')
    result = {}
    for model_id, limits in values.items():
        if not valid_id(model_id):
            raise UserError('逐模型上下文窗口包含无效模型 ID。')
        validated = validate_token_limits(limits)
        # Keep an explicit empty entry so it can override a legacy batch default.
        result[model_id] = validated
    return result


@dataclass(frozen=True)
class GatewayModel:
    id: str
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    limit_source: str = ''
    limit_source_url: str = ''
    input_display: str = ''
    output_display: str = ''
    limit_note: str = ''
    supports_images: bool | None = None
    supports_chat: bool | None = None  # Upstream declarations only; names are a later fallback.

    @property
    def token_limits(self):
        result = {}
        if self.max_input_tokens is not None:
            result['maxInputTokens'] = self.max_input_tokens
        if self.max_output_tokens is not None:
            result['maxOutputTokens'] = self.max_output_tokens
        return result


_INPUT_TOKEN_FIELDS = (
    'max_input_tokens', 'maxInputTokens', 'input_token_limit', 'inputTokenLimit',
    'max_prompt_tokens', 'maxPromptTokens', 'prompt_max_tokens',
)
_OUTPUT_TOKEN_FIELDS = (
    'max_output_tokens', 'maxOutputTokens', 'output_token_limit', 'outputTokenLimit',
    'max_completion_tokens', 'maxCompletionTokens',
)
_CONTEXT_TOKEN_FIELDS = (
    'context_length', 'contextLength', 'context_window', 'contextWindow',
    'max_context_length', 'maxContextLength', 'context_window_tokens',
)
_TOKEN_CONTAINER_FIELDS = (
    'limits', 'capabilities', 'metadata', 'top_provider', 'topProvider',
    'config', 'model_info', 'modelInfo', 'architecture',
    'model_hyper_params', 'modelHyperParams',
)


def _token_limit(value):
    """Return a safe positive integer or None for untrusted upstream metadata."""
    if type(value) is int:
        number = value
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        return None
    return number if 1 <= number <= MAX_TOKEN_LIMIT else None


def _token_sources(model):
    """Yield only allow-listed metadata containers, with top-level fields first."""
    queue = [(model, 0)]
    seen = set()
    while queue:
        current, depth = queue.pop(0)
        if not isinstance(current, dict) or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        if depth >= 3:
            continue
        for name in _TOKEN_CONTAINER_FIELDS:
            nested = current.get(name)
            if isinstance(nested, dict):
                queue.append((nested, depth + 1))


def _first_token_limit(model, fields):
    for source in _token_sources(model):
        for field_name in fields:
            if field_name in source:
                value = _token_limit(source[field_name])
                if value is not None:
                    return value
    return None


def _gateway_model(model):
    model_id = model.get('id')
    if not valid_id(model_id):
        return None
    max_input = _first_token_limit(model, _INPUT_TOKEN_FIELDS)
    max_output = _first_token_limit(model, _OUTPUT_TOKEN_FIELDS)
    context = _first_token_limit(model, _CONTEXT_TOKEN_FIELDS)
    if max_input is None and context is not None:
        # Some gateways expose only total context. When output is known, reserve
        # it; otherwise use total context as the safest available input fallback.
        if max_output is None:
            max_input = context
        elif context > max_output:
            max_input = context - max_output
    return GatewayModel(model_id, max_input, max_output, supports_images=image_input_support(model_id, model),
                        supports_chat=chat_metadata_support(model))


def _anthropic_model(model):
    """Read native Anthropic fields without giving generic ``max_tokens`` a new meaning."""
    model_id = model.get('id')
    if not valid_id(model_id):
        return None
    return GatewayModel(
        model_id,
        _token_limit(model.get('max_input_tokens')),
        _token_limit(model.get('max_tokens')),
        supports_images=image_input_support(model_id, model),
        supports_chat=chat_metadata_support(model),
    )


def _merge_catalog_model(catalog, model):
    previous = catalog.get(model.id)
    if previous is None:
        catalog[model.id] = model
        return
    # Duplicate records are merged conservatively: use the smaller declared
    # limit when two upstream entries disagree.
    input_values = [v for v in (previous.max_input_tokens, model.max_input_tokens) if v is not None]
    output_values = [v for v in (previous.max_output_tokens, model.max_output_tokens) if v is not None]
    catalog[model.id] = GatewayModel(
        model.id,
        min(input_values) if input_values else None,
        min(output_values) if output_values else None,
        supports_images=(False if False in (previous.supports_images, model.supports_images) else
                         True if True in (previous.supports_images, model.supports_images) else None),
        supports_chat=(False if False in (previous.supports_chat, model.supports_chat) else
                       True if True in (previous.supports_chat, model.supports_chat) else None),
    )


class ModelListError(UserError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward Authorization to a redirect destination, even same-host.
        return None


def _request_json(request):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise UserError('模型列表过大，已停止读取。')
        return json.loads(data)
    except urllib.error.HTTPError as e:
        messages = {401: '模型列表鉴权失败（HTTP 401），请检查密钥、鉴权方式与套餐接口。', 403: '该密钥没有读取模型列表的权限。',
                    404: '找不到模型列表接口，请检查地址是否包含正确的 /v1 前缀。',
                    429: '请求过于频繁，请稍后重试。'}
        code = e.code
        e.close()
        raise ModelListError(code, messages.get(code, '模型列表请求失败（HTTP %d）；未显示服务端响应以保护凭据。' % code)) from None
    except (OSError, ValueError, urllib.error.URLError):
        raise UserError('无法读取模型列表。请检查网络、HTTPS 证书和网关地址；本工具不使用系统代理。') from None


def _fetch_first_page(connection, api_key, query=None):
    headers = connection.headers(api_key, models=True)
    for url in connection.models_urls:
        suffix = '?' + urllib.parse.urlencode(query) if query else ''
        try:
            return url, _request_json(urllib.request.Request(url + suffix, headers=headers))
        except ModelListError as error:
            if error.status not in (404, 405):
                raise
    raise UserError('未找到可用的模型列表接口（HTTP 404/405）。该服务可能不提供模型列表；未调用聊天接口，也未切换到其他套餐地址。')


def fetch_model_catalog(base_url, api_key, allow_private_http=False, auth='auto'):
    connection = resolve_connection(base_url, 'openai', auth, allow_private_http)
    _, obj = _fetch_first_page(connection, api_key)
    return _enrich_catalog(connection, _parse_openai_catalog(obj))


def _enrich_catalog(connection, catalog):
    from official_limits import enrich_catalog
    return enrich_catalog(connection, catalog)


def _parse_openai_catalog(obj):
    if not isinstance(obj, dict) or not isinstance(obj.get('data'), list):
        raise UserError('网关应返回 OpenAI 风格的模型清单：data 数组。')
    catalog = {}
    for record in obj['data']:
        if not isinstance(record, dict):
            continue
        model = _gateway_model(record)
        if model is None:
            continue
        _merge_catalog_model(catalog, model)
    if len(catalog) > MAX_MODELS:
        raise UserError('模型超过 10000 个，已停止导入。')
    return sorted(catalog.values(), key=lambda model: model.id.casefold())


def fetch_anthropic_model_catalog(base_url, api_key, allow_private_http=False, auth='auto'):
    """Read all pages from a native Anthropic-compatible ``GET /v1/models`` API."""
    connection = resolve_connection(base_url, 'anthropic', auth, allow_private_http)
    if connection.models_protocol == 'openai':
        _, obj = _fetch_first_page(connection, api_key)
        return _enrich_catalog(connection, _parse_openai_catalog(obj))
    headers = connection.headers(api_key, models=True)
    models_url = None
    catalog = {}
    after_id = None
    seen_cursors = set()
    page_count = 0
    while True:
        query = {'limit': 1000}
        if after_id is not None:
            query['after_id'] = after_id
        if models_url is None:
            models_url, obj = _fetch_first_page(connection, api_key, query)
        else:
            obj = _request_json(urllib.request.Request(
                models_url + '?' + urllib.parse.urlencode(query), headers=headers,
            ))
        if not isinstance(obj, dict) or not isinstance(obj.get('data'), list):
            raise UserError('网关应返回 Anthropic 风格的模型清单：data 数组。')
        for record in obj['data']:
            if not isinstance(record, dict):
                continue
            model = _anthropic_model(record)
            if model is not None:
                _merge_catalog_model(catalog, model)
                if len(catalog) > MAX_MODELS:
                    raise UserError('模型超过 10000 个，已停止导入。')
        has_more = obj.get('has_more', False)
        if type(has_more) is not bool:
            raise UserError('Anthropic 模型列表分页信息不合法。')
        if not has_more:
            break
        cursor = obj.get('last_id')
        if not valid_id(cursor) or cursor in seen_cursors:
            raise UserError('Anthropic 模型列表分页游标异常，已停止读取。')
        seen_cursors.add(cursor)
        after_id = cursor
        page_count += 1
        if page_count >= MAX_MODELS:
            raise UserError('Anthropic 模型列表分页异常，已停止读取。')
    return _enrich_catalog(connection, sorted(catalog.values(), key=lambda model: model.id.casefold()))


def fetch_models(base_url, api_key, allow_private_http=False):
    """Compatibility helper returning IDs only."""
    return [model.id for model in fetch_model_catalog(
        base_url, api_key, allow_private_http=allow_private_http,
    )]


API_ONLY_IMPORT_HINT = '仅支持API导入'
UNSUPPORTED_IMPORT_HINT = '不支持导入到目标应用'


def unsupported_chat_model(model_id, declared=None):
    return chat_model_support(model_id, declared) is False


def validate_chat_models(ids, chat_support_by_model=None):
    values = {} if chat_support_by_model is None else chat_support_by_model
    if not isinstance(values, dict) or len(values) > MAX_MODELS or any(
        not valid_id(mid) or (value is not None and type(value) is not bool)
        for mid, value in values.items()
    ):
        raise UserError('逐模型类型设置不合法。')
    if any(unsupported_chat_model(mid, values.get(mid)) for mid in ids):
        raise UserError(UNSUPPORTED_IMPORT_HINT + '，请选择对话或视觉理解模型。')


def image_video_hint(model_id):
    """Presentation hint for image/video models, not a capability guarantee."""
    name = model_id.casefold()
    words = ('seedream', 'seedance', 'minimax-h3', 'dall-e', 'stable-diffusion',
             'flux', 'sora', 'video', 'image-generation', 'gpt-image', 'imagen')
    return any(word in name for word in words) or (
        name.startswith('gemini-') and re.search(r'-image(?:-|$)', name) is not None)


def media_hint(model_id):
    # A presentation hint only, never a claim about real capabilities.
    words = ('seedream', 'seedance', 'minimax-h3', 'embedding', 'whisper', 'tts',
             'text-to-speech', 'dall-e', 'stable-diffusion', 'flux', 'rerank', 'sora', 'video', 'image-generation')
    return image_video_hint(model_id) or any(w in model_id.casefold() for w in words)


def read_snapshot(path):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink() or (hasattr(path.parent, 'is_junction') and path.parent.is_junction()):
        raise UserError('为避免写错位置，不支持链接或目录联接形式的模型配置路径。')
    try:
        if not path.exists():
            return None
        with path.open('rb') as f:
            raw = f.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise UserError('本地模型配置过大，已停止读取。')
        return raw
    except OSError:
        raise UserError('无法读取本地模型配置，请检查文件权限。') from None


def parse_document(raw):
    if raw is None:
        return []
    try:
        obj = json.loads(raw.decode('utf-8-sig'))
    except (ValueError, UnicodeError):
        raise UserError('现有模型配置不是有效 JSON；不会覆盖或自动重建它。') from None
    models = obj if isinstance(obj, list) else obj.get('models') if isinstance(obj, dict) else None
    if not isinstance(models, list):
        raise UserError('未知模型配置结构；仅支持数组或包含 models 数组的对象。')
    ids = set()
    for m in models:
        if not isinstance(m, dict) or not valid_id(m.get('id')):
            raise UserError('现有配置包含无法识别的模型条目；为保留原数据已停止。')
        if m['id'] in ids:
            raise UserError('现有配置包含重复模型 ID，请先在 WorkBuddy 中整理。')
        ids.add(m['id'])
    return obj


def model_array(obj):
    return obj if isinstance(obj, list) else obj['models']


@dataclass(repr=False)
class Plan:
    original: bytes | None = field(repr=False)
    document: object = field(repr=False)
    rows: list = field(default_factory=list)

    @property
    def changed(self):
        return any(action in ('新增', '更新') for _, action in self.rows)

    def summary(self):
        parts = []
        for action in ('新增', '更新', '保留已有', '保留其他连接', '无变化'):
            count = sum(a == action for _, a in self.rows)
            if count:
                parts.append('%s %d' % (action, count))
        return '；'.join(parts)


def validate_model_capabilities(values):
    if values is None:
        return {}
    if not isinstance(values, dict) or len(values) > MAX_MODELS:
        raise UserError('逐模型能力设置不合法。')
    result = {}
    for mid, flags in values.items():
        if not valid_id(mid) or not isinstance(flags, dict) or any(
                k not in ('supportsImages', 'supportsToolCall', 'supportsReasoning') or type(v) is not bool
                for k, v in flags.items()):
            raise UserError('逐模型能力设置不合法。')
        result[mid] = dict(flags)
    return result


def model_capabilities(model_id, overrides, per_model):
    detected = image_input_support(model_id)
    result = {'supportsImages': True} if detected is True else {}
    result.update(per_model.get(model_id, {}))
    result.update(overrides)  # Explicit user choice always wins.
    return result


def make_plan(raw, base_url, api_key, ids, capabilities=None, update_existing=False,
              allow_private_http=False, token_limits=None, token_limits_by_model=None, capabilities_by_model=None,
              chat_support_by_model=None):
    url, key = normalize_url(base_url, allow_private_http=allow_private_http), check_key(api_key)
    endpoint = url.rstrip('/') + '/chat/completions'
    per_model = validate_model_capabilities(capabilities_by_model)
    document = copy.deepcopy(parse_document(raw))
    models = model_array(document)
    index = {m['id']: i for i, m in enumerate(models)}
    flags = capabilities or {}
    default_limits = validate_token_limits(token_limits)
    model_limits = validate_model_token_limits(token_limits_by_model)
    if any(k not in ('supportsToolCall', 'supportsImages', 'supportsReasoning') or type(v) is not bool for k, v in flags.items()):
        raise UserError('模型能力设置不合法。')
    if not ids or len(ids) > 10000 or any(not valid_id(x) for x in ids):
        raise UserError('请至少选择一个有效模型；每批最多 10000 个。')
    validate_chat_models(ids, chat_support_by_model)
    rows = []
    for mid in dict.fromkeys(ids):
        limits = model_limits.get(mid, default_limits)
        pos = index.get(mid)
        old = models[pos] if pos is not None else None
        # Do not repoint a native-vendor/custom-protocol entry behind the user's back.
        if old is not None and (old.get('vendor') != 'Custom' or old.get('useCustomProtocol', False)):
            rows.append((mid, '保留已有'))
            continue
        # The overwrite choice only applies to the same model on the same gateway.
        # A same-ID model connected elsewhere is never silently repointed.
        if old is not None and not same_gateway_url(old.get('url'), url):
            rows.append((mid, '保留其他连接'))
            continue
        if old is not None and not update_existing:
            rows.append((mid, '保留已有'))
            continue
        new = copy.deepcopy(old) if old is not None else {
            'id': mid, 'name': mid, 'supportsToolCall': False,
            'supportsImages': False, 'supportsReasoning': False}
        new.update(vendor='Custom', url=endpoint, apiKey=key, useCustomProtocol=False)
        new.update(model_capabilities(mid, flags, per_model))
        new.update(limits)
        if old is None:
            models.append(new)
            index[mid] = len(models) - 1
            rows.append((mid, '新增'))
        elif old == new:
            rows.append((mid, '无变化'))
        else:
            models[pos] = new
            rows.append((mid, '更新'))
    return Plan(raw, document, rows)


def ensure_workbuddy_stopped():
    if os.name != 'nt':
        raise UserError('直接导入与恢复目前仅支持 Windows。')
    script = "$ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(WorkBuddy)(\\.exe)?$' -or ($_.Name -match '^(node|codebuddy)(\\.exe)?$' -and ($_.ExecutablePath -match 'WorkBuddy' -or $_.CommandLine -match 'workbuddy')) }).Count"
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode != 0 or not result.stdout.strip().isdigit():
            raise UserError('无法确认 WorkBuddy 是否已退出；为安全起见停止写入。')
        if int(result.stdout.strip()):
            raise UserError('请完全退出 WorkBuddy（包括系统托盘）后再导入或恢复；工具不会强制结束应用。')
    except (OSError, subprocess.TimeoutExpired):
        raise UserError('无法检查 WorkBuddy 运行状态，未写入任何配置。') from None


def dpapi(data, decrypt=False):
    if os.name != 'nt':
        raise UserError('加密备份需要 Windows 当前用户凭据保护。')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    fn = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
                   ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise UserError('无法处理加密备份；恢复必须使用创建备份的 Windows 账户。')
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def _sid():
    import csv
    result = subprocess.run(['whoami.exe', '/user', '/fo', 'csv', '/nh'], capture_output=True,
                            timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise UserError('无法确定当前 Windows 用户，已停止写入。')
    sid = next(csv.reader(result.stdout.decode('utf-8', errors='replace').splitlines()))[-1]
    if not sid.startswith('S-1-') or not all(c in 'S-0123456789' for c in sid):
        raise UserError('无法确定当前 Windows 用户，已停止写入。')
    return sid


def restrict_file(path):
    if os.name == 'nt':
        r = subprocess.run(['icacls.exe', str(path), '/inheritance:r', '/grant:r',
                            '*' + _sid() + ':F', '*S-1-5-18:F'], capture_output=True,
                           timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode:
            raise UserError('无法设置配置文件的私有权限，已停止写入。')
    else:
        os.chmod(path, 0o600)


def atomic_write(path, data):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
    temp = Path(name)
    try:
        # Apply the ACL before writing any credential bytes.
        restrict_file(temp)
        with os.fdopen(fd, 'wb') as f:
            fd = None
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if fd is not None:
            os.close(fd)
        if temp.exists():
            temp.unlink()


@contextmanager
def model_lock(path):
    lock = Path(path).with_name('.' + Path(path).name + '.workbuddy.lock')
    try:
        lock.mkdir()
    except FileExistsError:
        raise UserError('WorkBuddy 模型配置正在被占用（存在文件锁）；不会抢占或删除该锁。') from None
    stop = threading.Event()
    broken = threading.Event()
    identity = lock.stat().st_ino
    def heartbeat():
        while not stop.wait(2):
            try:
                if lock.stat().st_ino != identity:
                    broken.set()
                    return
                os.utime(lock, None)
            except OSError:
                broken.set()
                return
    def check():
        if broken.is_set() or not lock.exists() or lock.stat().st_ino != identity:
            raise UserError('模型文件锁发生变化，已停止写入。')
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        yield check
    finally:
        stop.set()
        thread.join(timeout=3)
        try:
            if lock.stat().st_ino == identity:
                lock.rmdir()  # Never recursively delete; leave a compromised lock untouched.
        except OSError:
            pass


def backup_snapshot(path, raw):
    body = json.dumps({'version': 1, 'target': str(Path(path).resolve()),
                       'raw': base64.b64encode(raw).decode() if raw is not None else None},
                      ensure_ascii=False).encode('utf-8')
    encrypted = dpapi(body)
    folder = Path(path).parent / 'gateway-model-assistant-backups'
    if folder.is_symlink() or (hasattr(folder, 'is_junction') and folder.is_junction()):
        raise UserError('备份目录不能是链接或目录联接。')
    folder.mkdir(exist_ok=True)
    name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:10] + '.wbbackup'
    result = folder / name
    atomic_write(result, b'WBGA1\n' + base64.b64encode(encrypted))
    return result


def apply_plan(path, plan):
    path = Path(path)
    ensure_workbuddy_stopped()
    if not path.parent.is_dir():
        raise UserError('未找到 WorkBuddy 配置目录，请先安装并启动一次 WorkBuddy。')
    # Validate the serialized payload before touching the destination.
    data = (json.dumps(plan.document, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    if len(data) > MAX_BYTES:
        raise UserError('导入后的配置过大，已停止写入。')
    parse_document(data)
    with model_lock(path) as check:
        if read_snapshot(path) != plan.original:
            raise UserError('配置在预览后发生变化，请重新预览，避免覆盖其他修改。')
        if not plan.changed:
            return None
        backup = backup_snapshot(path, plan.original)
        ensure_workbuddy_stopped()
        check()
        if read_snapshot(path) != plan.original:
            raise UserError('配置已被其他程序修改，请重新预览。')
        atomic_write(path, data)
        return backup


def restore_backup(path, backup_path):
    path = Path(path)
    try:
        with Path(backup_path).open('rb') as f:
            data = f.read(12 * MAX_BYTES + 1)
        if len(data) > 12 * MAX_BYTES or not data.startswith(b'WBGA1\n'):
            raise ValueError()
        obj = json.loads(dpapi(base64.b64decode(data[6:], validate=True), decrypt=True))
        if obj['version'] != 1 or Path(obj['target']).resolve() != path.resolve():
            raise ValueError()
        raw = base64.b64decode(obj['raw'], validate=True) if obj['raw'] is not None else None
        parse_document(raw)
    except (OSError, ValueError, KeyError, TypeError):
        raise UserError('这不是适用于当前配置文件的有效加密备份。') from None
    ensure_workbuddy_stopped()
    with model_lock(path) as check:
        current = read_snapshot(path)
        safety = backup_snapshot(path, current)
        ensure_workbuddy_stopped()
        check()
        if read_snapshot(path) != current:
            raise UserError('恢复过程中配置发生变化，已停止。')
        if raw is None:
            if path.exists():
                path.unlink()
        else:
            atomic_write(path, raw)
    return safety


def detection(home=None, appdata=None):
    home = Path(home) if home else Path.home()
    appdata = Path(appdata) if appdata else Path(os.environ.get('APPDATA', home / 'AppData/Roaming'))
    return {'workbuddy': (home / '.workbuddy').is_dir(),
            'trae': (appdata / 'Trae').is_dir(), 'trae_solo_cn': (appdata / 'TRAE SOLO CN').is_dir()}
