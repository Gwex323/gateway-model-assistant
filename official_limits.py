"""Public provider specifications. Never receives or transmits an API key."""
from dataclasses import replace
from decimal import Decimal
from html.parser import HTMLParser
import re
import urllib.parse
import urllib.request

PRICING_URL = 'https://api-docs.deepseek.com/quick_start/pricing/'
API_URL = 'https://api-docs.deepseek.com/api/create-chat-completion/'
MAX_BYTES = 4 * 1024 * 1024
MAX_LIMIT = 100_000_000


class PublicDocsRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if (target.scheme != 'https' or target.hostname != 'api-docs.deepseek.com'
                or target.port not in (None, 443) or target.username or target.password):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_document(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), PublicDocsRedirect())
    request = urllib.request.Request(url, headers={'Accept': 'text/html',
                                                  'User-Agent': 'GatewayModelAssistant/0.12.8'})
    with opener.open(request, timeout=8) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('Public document too large')
    return data.decode('utf-8')


class SpecHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.text = []
        self.table = None
        self.row = None
        self.cell = None
        self.span = 1
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'sup'):
            self.skip += 1
        if tag == 'table':
            self.table = []
        elif tag == 'tr':
            self.row = []
        elif tag in ('td', 'th'):
            attrs = dict(attrs)
            self.span = int(attrs.get('colspan', '1'))
            if not 1 <= self.span <= 64:
                raise ValueError('Unexpected table width')
            self.cell = []
        elif tag == 'br' and self.cell is not None:
            self.cell.append(' ')

    def handle_data(self, data):
        if self.skip:
            return
        self.text.append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'sup'):
            self.skip = max(0, self.skip - 1)
        if tag in ('td', 'th') and self.cell is not None:
            value = ' '.join(''.join(self.cell).split())
            if self.row is not None:
                self.row.extend([value] * self.span)
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            if self.table is not None:
                self.table.append(self.row)
            self.row = None
        elif tag == 'table' and self.table is not None:
            self.tables.append(self.table)
            self.table = None


def _amount(value, exact_units):
    value = value.strip().upper()
    if value in exact_units:
        return exact_units[value]
    match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*([KM]?)', value)
    if not match:
        return None
    # Unexpanded context shorthand is a conservative decimal lower bound.
    # Output limits require an exact value from the API reference below.
    number = int(Decimal(match[1]) * {'': 1, 'K': 1000, 'M': 1000000}[match[2]])
    return number if 0 < number <= MAX_LIMIT else None


def parse_deepseek_specs(pricing_html, api_html):
    pricing, api = SpecHTML(), SpecHTML()
    pricing.feed(pricing_html)
    api.feed(api_html)
    api_text = ' '.join(' '.join(api.text).split())
    # The API reference expands output shorthand, e.g. 384K (393216).
    exact = {}
    for unit, raw in re.findall(r'(\d+(?:\.\d+)?[KM])\s*\(([\d,]+)\)', api_text, re.I):
        number = int(raw.replace(',', ''))
        unit = unit.upper()
        if not 0 < number <= MAX_LIMIT or unit in exact and exact[unit] != number:
            raise ValueError('Conflicting official units')
        exact[unit] = number
    specs = {}
    for table in pricing.tables:
        rows = {row[0].upper(): row for row in table if row}
        names, contexts, outputs = (rows.get(key) for key in ('MODEL', 'CONTEXT LENGTH', 'MAX OUTPUT'))
        if not names or not contexts or not outputs:
            continue
        if not len(names) == len(contexts) == len(outputs):
            continue
        for index, name in enumerate(names):
            if not re.fullmatch(r'deepseek-[a-z0-9.-]+', name):
                continue
            context = _amount(contexts[index], exact)
            output_text = outputs[index].upper()
            maximum = re.search(r'(?:MAXIMUM|MAX)\s*:\s*(\d+(?:\.\d+)?[KM]?)', output_text)
            output_unit = maximum[1] if maximum else output_text.strip()
            # Do not guess output ceilings from an ambiguous K/M abbreviation.
            output = exact.get(output_unit) if re.search('[KM]', output_unit) else _amount(output_unit, exact)
            if context and output and context > output:
                value = (context, output)
                if name in specs and specs[name] != value:
                    raise ValueError('Conflicting official model specifications')
                specs[name] = value
    # Only inherit limits for aliases EXPLICITLY mapped by the official page.
    pricing_text = ' '.join(' '.join(pricing.text).split())
    for target, legacy in re.findall(
            r'Use (deepseek-[a-z0-9.-]+) as the model name\. The legacy names (.*?) are still accepted,.*?their requests are served by',
            pricing_text):
        if target not in specs:
            continue
        for alias in re.findall(r'deepseek-[a-z0-9.-]+', legacy):
            if alias in specs and specs[alias] != specs[target]:
                raise ValueError('Conflicting official alias specification')
            specs[alias] = specs[target]
    return specs


def fetch_deepseek_specs():
    return parse_deepseek_specs(_read_document(PRICING_URL), _read_document(API_URL))


def enrich_catalog(connection, catalog):
    parsed = urllib.parse.urlsplit(connection.base_url)
    # SGT is explicitly treated as having the official model limits by request.
    # Other third-party gateways keep their existing isolation policy.
    if (parsed.scheme == 'https' and parsed.hostname == 'platform.sgt.site'
            and parsed.port in (None, 443) and parsed.path == '/v1'
            and not parsed.username and not parsed.password):
        from provider_specs import enrich_sgt_catalog
        return enrich_sgt_catalog(catalog)
    if (parsed.scheme != 'https' or parsed.hostname != 'api.deepseek.com'
            or parsed.port not in (None, 443) or parsed.path not in ('/v1', '/anthropic/v1')):
        return catalog
    missing = [model for model in catalog if model.id.startswith('deepseek-')
               and (model.max_input_tokens is None or model.max_output_tokens is None)]
    if not missing:
        return catalog
    missing_ids = {model.id for model in missing}
    try:
        specs = fetch_deepseek_specs()
    except (OSError, ValueError, UnicodeError):
        specs = {}
    result = []
    for model in catalog:
        if model.id not in missing_ids:
            result.append(model)
            continue
        spec = specs.get(model.id)
        if spec is None:
            result.append(replace(model, limit_source='官方规格未获取或未匹配'))
            continue
        context, output = spec
        # Existing upstream limits take priority, and must fit the context.
        effective_output = model.max_output_tokens if model.max_output_tokens is not None else output
        if effective_output >= context or (model.max_input_tokens is not None
                and model.max_input_tokens + effective_output > context):
            result.append(replace(model, limit_source='接口与官方规格冲突，未补值'))
            continue
        result.append(replace(model,
            max_input_tokens=model.max_input_tokens if model.max_input_tokens is not None else context-effective_output,
            max_output_tokens=effective_output,
            limit_source='官方规格·保守输入预算' if model.max_input_tokens is None else '接口＋官方规格',
            limit_source_url=PRICING_URL))
    return result
