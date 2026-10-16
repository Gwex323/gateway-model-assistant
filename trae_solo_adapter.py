"""TraeWork CN persistent import through the client's own model service.

The client is launched briefly with a loopback-only DevTools endpoint.  Code runs
inside the authenticated TRAE renderer and asks its own connection service to call
``model/add_custom_model``.  No TRAE login token is read, copied, logged, or stored.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import time
from dataclasses import dataclass, field
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen
import winreg

import core
import trae_adapter


APP_NAME = trae_adapter.TRAE_WORK_CN_NAME
INTERNAL_NAME = trae_adapter.TRAE_WORK_CN_INTERNAL_NAME
# Kept as a compatibility alias for the adapter module and existing callers.
SOLO_NAME = APP_NAME
DEBUG_HOST = "127.0.0.1"
PROVIDERS = {
    "openai": "custom_openai_compatible",
    "anthropic": "custom_anthropic_compatible",
}


@dataclass(repr=False)
class Plan:
    target: trae_adapter.Target = field(repr=False)
    base_url: str = field(repr=False)
    api_key: str = field(repr=False)
    protocol: str = "openai"
    provider: str = "custom_openai_compatible"
    endpoint: str = field(repr=False, default="")
    auth: str = 'auto'
    ids: list[str] = field(repr=False, default_factory=list)
    capabilities: dict[str, bool] = field(repr=False, default_factory=dict)
    model_capabilities: dict[str, dict[str, bool]] = field(repr=False, default_factory=dict)
    token_limits: dict[str, int] = field(repr=False, default_factory=dict)
    model_token_limits: dict[str, dict[str, int]] = field(repr=False, default_factory=dict)
    display_names: dict[str, str] = field(default_factory=dict)
    update_existing: bool = False
    rows: list[tuple[str, str]] = field(default_factory=list)

    @property
    def changed(self):
        return bool(self.ids)

    @property
    def compatibility_warning(self):
        if self.protocol == 'anthropic' and self.auth == 'bearer':
            return '上游要求 Anthropic Bearer 鉴权；尚未验证 TraeWork CN 自定义模型会发送该请求头。导入只保存配置，不代表模型调用成功。'
        return ''

    def summary(self):
        policy = "覆盖" if self.update_existing else "保留"
        protocol = "Anthropic 原生" if self.protocol == "anthropic" else "OpenAI 兼容"
        recognized = sum(bool(self.model_token_limits.get(mid)) for mid in self.ids)
        limit_text = f"，自动识别 Token 上限 {recognized}/{len(self.ids)} 个" if self.ids else ""
        return f"由客户端以 {protocol} 持久化导入 {len(self.ids)} 个（同 URL 已有模型：{policy}{limit_text}，执行时复核）\n上游鉴权：{self.auth}；请求地址：{self.endpoint}"


@dataclass(frozen=True)
class ApplyResult:
    added: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    kept: tuple[str, ...] = ()

    @property
    def changed(self):
        return bool(self.added or self.updated)


def make_plan(target, base_url, api_key, ids, capabilities=None, update_existing=False,
              allow_private_http=False, token_limits=None, token_limits_by_model=None,
              protocol="openai", auth='auto', display_names_by_model=None, capabilities_by_model=None,
              chat_support_by_model=None):
    target = target if isinstance(target, trae_adapter.Target) else trae_adapter.Target(SOLO_NAME, Path(target), SOLO_NAME)
    if target.name != SOLO_NAME:
        raise core.UserError(f"TRAE 客户端持久化导入仅适用于 {APP_NAME}。")
    protocol = str(protocol).strip().lower()
    if protocol not in PROVIDERS:
        raise core.UserError("TraeWork CN 导入协议不受支持。")
    connection = core.resolve_connection(base_url, protocol, auth, allow_private_http)
    url = connection.base_url
    endpoint = url if protocol == "openai" else connection.endpoint
    key = core.check_key(api_key)
    if not ids or len(ids) > 10000 or any(not core.valid_id(mid) for mid in ids):
        raise core.UserError("请至少选择一个有效模型；每批最多 10000 个。")
    core.validate_chat_models(ids, chat_support_by_model)
    flags = capabilities or {}
    per_model = core.validate_model_capabilities(capabilities_by_model)
    display_names = trae_adapter.validate_display_names(display_names_by_model, ids)
    default_limits = core.validate_token_limits(token_limits)
    supplied_model_limits = core.validate_model_token_limits(token_limits_by_model)
    if any(k not in ("supportsImages", "supportsToolCall", "supportsReasoning") or type(v) is not bool
           for k, v in flags.items()):
        raise core.UserError("模型能力设置不合法。")
    normalized = list(dict.fromkeys(ids))
    model_limits = {}
    for mid in normalized:
        limits = supplied_model_limits.get(mid, default_limits)
        if limits:
            model_limits[mid] = dict(limits)
    return Plan(
        target=target, base_url=url, api_key=key, ids=normalized,
        protocol=protocol, provider=PROVIDERS[protocol], endpoint=endpoint,
        auth=connection.auth,
        capabilities=dict(flags), token_limits=default_limits,
        model_capabilities={mid: core.model_capabilities(mid, flags, per_model) for mid in normalized},
        model_token_limits=model_limits,
        display_names=dict(display_names),
        update_existing=bool(update_existing),
        rows=[(mid, "导入时复核") for mid in normalized],
    )


def _candidate_install_locations():
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    program_files = [Path(os.environ.get(name, "")) for name in ("ProgramFiles", "ProgramFiles(x86)")]
    yield Path(r"D:\application\TRAE SOLO CN\TRAE SOLO CN.exe")
    yield local / f"Programs/{INTERNAL_NAME}/{INTERNAL_NAME}.exe"
    yield local / f"{INTERNAL_NAME}/{INTERNAL_NAME}.exe"
    for root in program_files:
        yield root / f"{INTERNAL_NAME}/{INTERNAL_NAME}.exe"


def _registry_install_locations():
    locations = (
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for hive, path in locations:
        try:
            root = winreg.OpenKey(hive, path)
        except OSError:
            continue
        with root:
            for index in range(winreg.QueryInfoKey(root)[0]):
                try:
                    with winreg.OpenKey(root, winreg.EnumKey(root, index)) as item:
                        name = str(winreg.QueryValueEx(item, "DisplayName")[0])
                        if (APP_NAME.casefold() not in name.casefold()
                                and INTERNAL_NAME.casefold() not in name.casefold()):
                            continue
                        try:
                            location = Path(str(winreg.QueryValueEx(item, "InstallLocation")[0]))
                            yield location / f"{INTERNAL_NAME}.exe"
                        except OSError:
                            pass
                        try:
                            icon = str(winreg.QueryValueEx(item, "DisplayIcon")[0]).strip('"').split(",")[0]
                            yield Path(icon)
                        except OSError:
                            pass
                except OSError:
                    continue


def discover_executable():
    override = os.environ.get("TRAE_WORK_CN_EXE") or os.environ.get("TRAE_SOLO_CN_EXE")
    candidates = ([Path(override)] if override else []) + list(_candidate_install_locations()) + list(_registry_install_locations())
    seen = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        marker = str(resolved).casefold()
        if marker in seen:
            continue
        seen.add(marker)
        accepted_names = {f"{INTERNAL_NAME}.exe".casefold(), f"{APP_NAME}.exe".casefold()}
        if resolved.is_file() and resolved.name.casefold() in accepted_names:
            return resolved
    raise core.UserError(f"未找到 {APP_NAME} 程序，请确认已安装，或设置 TRAE_WORK_CN_EXE。")


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((DEBUG_HOST, 0))
        return sock.getsockname()[1]


class _WebSocket:
    def __init__(self, url, timeout=15):
        parsed = urlparse(url)
        if parsed.scheme != "ws" or parsed.hostname not in ("127.0.0.1", "localhost"):
            raise core.UserError("TRAE 调试连接地址不安全，已停止导入。")
        self.sock = socket.create_connection((parsed.hostname, parsed.port or 80), timeout=timeout)
        self.sock.settimeout(timeout)
        self._buffer = bytearray()
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port or 80}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        self.sock.sendall(request)
        response = self._read_headers()
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if not response.startswith(b"HTTP/1.1 101") or b"sec-websocket-accept: " + expected.lower() not in response.lower():
            self.close()
            raise core.UserError(f"无法建立 {APP_NAME} 的本机安全连接。")

    def _read_headers(self):
        data = bytearray()
        while b"\r\n\r\n" not in data and len(data) < 65536:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            data.extend(chunk)
        marker = data.find(b"\r\n\r\n")
        if marker < 0:
            return bytes(data)
        end = marker + 4
        self._buffer.extend(data[end:])
        return bytes(data[:end])

    def _read_exact(self, size):
        data = bytearray()
        if self._buffer:
            take = min(size, len(self._buffer))
            data.extend(self._buffer[:take])
            del self._buffer[:take]
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise ConnectionError("websocket closed")
            data.extend(chunk)
        return bytes(data)

    def send_text(self, value):
        payload = value.encode("utf-8")
        header = bytearray([0x81])
        size = len(payload)
        if size < 126:
            header.append(0x80 | size)
        elif size <= 0xFFFF:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", size))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", size))
        mask = secrets.token_bytes(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def recv_text(self):
        fragments = bytearray()
        while True:
            first, second = self._read_exact(2)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            size = second & 0x7F
            if size == 126:
                size = struct.unpack("!H", self._read_exact(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else None
            payload = self._read_exact(size)
            if mask:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 0x8:
                raise ConnectionError("websocket closed")
            if opcode == 0x9:
                self._send_control(0xA, payload)
                continue
            if opcode in (0x0, 0x1):
                fragments.extend(payload)
                if first & 0x80:
                    return fragments.decode("utf-8")

    def _send_control(self, opcode, payload=b""):
        mask = secrets.token_bytes(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(bytes([0x80 | opcode, 0x80 | len(payload)]) + mask + masked)

    def close(self):
        sock = getattr(self, "sock", None)
        self.sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


class _CDP:
    def __init__(self, websocket_url):
        self.ws = _WebSocket(websocket_url)
        self.counter = 0

    def call(self, method, params=None, timeout=60):
        self.counter += 1
        request_id = self.counter
        self.ws.sock.settimeout(timeout)
        self.ws.send_text(json.dumps({"id": request_id, "method": method, "params": params or {}}, separators=(",", ":")))
        while True:
            message = json.loads(self.ws.recv_text())
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message.get("error") or {}
                code = error.get("code")
                raise _CDPProtocolError(code if isinstance(code, int) else None)
            return message.get("result", {})

    def close(self):
        self.ws.close()


class _CDPProtocolError(Exception):
    def __init__(self, code):
        super().__init__("cdp protocol error")
        self.code = code


def _wait_target(port, deadline):
    endpoint = f"http://{DEBUG_HOST}:{port}/json/list"
    fallback = None
    fallback_since = None
    while time.monotonic() < deadline:
        try:
            with urlopen(endpoint, timeout=1) as response:
                targets = json.load(response)
            pages = [item for item in targets if item.get("type") == "page" and item.get("webSocketDebuggerUrl")]
            if pages:
                preferred = [item for item in pages if any(
                    marker in str(item.get("url", "")).casefold()
                    for marker in ("solo-lite.html", "workbench")
                )]
                if preferred:
                    preferred.sort(key=lambda item: str(item.get("url", "")))
                    return preferred[0]["webSocketDebuggerUrl"]
                pages.sort(key=lambda item: str(item.get("url", "")))
                fallback = pages[0]["webSocketDebuggerUrl"]
                if fallback_since is None:
                    fallback_since = time.monotonic()
                # Cold starts often expose a splash/devtools page first. Give the
                # actual workbench enough time to appear before using a fallback.
                if time.monotonic() - fallback_since >= 30:
                    return fallback
        except (OSError, URLError, ValueError, json.JSONDecodeError):
            pass
        time.sleep(0.25)
    if fallback:
        return fallback
    raise core.UserError(f"等待 {APP_NAME} 启动超时，请稍后重试。")


_WORKBENCH_SOURCE_EXPRESSION = r"""(()=>{
  const valid=(value,extension)=>{
    try{
      const url=new URL(value);
      if(!['vscode-file:','file:'].includes(url.protocol)) return null;
      const pattern=extension==='css'
        ? /\/out\/vs\/workbench\/workbench\.desktop\.main\.solo-lite(?:-slim)?\.css$/
        : /\/out\/vs\/workbench\/workbench\.desktop\.main\.solo-lite(?:-slim)?\.js$/;
      if(!pattern.test(url.pathname)) return null;
      if(extension==='css') url.pathname=url.pathname.replace(/\.css$/,'.js');
      return url.href;
    }catch{return null;}
  };
  const styles=typeof document==='undefined'?[]:
    [...document.querySelectorAll('link[rel~="stylesheet"][href]')].map(x=>x.href);
  const candidates=[...new Set(styles.map(x=>valid(x,'css')).filter(Boolean))];
  // Never guess between two loaded workbench variants.
  if(candidates.length) return candidates.length===1?candidates[0]:null;
  const resources=typeof performance==='undefined'?[]:
    [...performance.getEntriesByType('resource')].map(x=>x.name);
  const scripts=[...new Set(resources.map(x=>valid(x,'js')).filter(Boolean))];
  return scripts.length===1?scripts[0]:null;
})()"""


def _expression(payload):
    payload = dict(payload)
    payload.setdefault("protocol", "openai")
    payload.setdefault("provider", PROVIDERS["openai"])
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f"""(async()=>{{
const input={data};
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const isWorkbench=value=>typeof value?.createDecorator==='function'&&!!value?.icubeStore;
let wb;
for(let attempt=0;attempt<120&&!wb;attempt++){{
  if(window.workbenchPromise){{
    try{{
      const loaded=await window.workbenchPromise;
      const candidate=loaded?.result||loaded;
      if(isWorkbench(candidate)) wb=candidate;
    }}catch{{}}
  }}
  if(!wb){{
    const src={_WORKBENCH_SOURCE_EXPRESSION};
    if(src){{
      try{{
        const candidate=await import(src);
        if(isWorkbench(candidate)) wb=candidate;
      }}catch{{}}
    }}
  }}
  if(!wb) await sleep(250);
}}
if(!wb) throw new Error('workbench_module_missing');
const serviceId=wb.createDecorator('IICubeAiChatConnectionService');
let collection;
let service;
for(let attempt=0;attempt<120&&!service?.getDefaultClient;attempt++){{
  collection=wb.icubeStore?.serviceCollection;
  if(collection){{
    service=collection.get(serviceId);
    if(!service?.getDefaultClient){{
      const instantiation=collection.get(wb.createDecorator('instantiationService'));
      if(instantiation?.resolveSingletonInstance) service=instantiation.resolveSingletonInstance(serviceId,service);
      else if(instantiation?.invokeFunction) service=instantiation.invokeFunction(a=>a.get(serviceId));
    }}
  }}
  if(!service?.getDefaultClient) await sleep(250);
}}
if(!collection) throw new Error('service_collection_missing');
if(!service?.getDefaultClient) throw new Error('connection_service_missing');
const client=await service.getDefaultClient();
const request=(method,data)=>client.request({{service:'model',method,data}});
const provider=String(input.provider||'custom_openai_compatible');
const idOf=m=>{{
  const name=typeof m?.name==='string'?m.name:'';
  for(const prefix of ['custom_openai_compatible//','custom_anthropic_compatible//']){{
    if(name.startsWith(prefix)) return name.slice(prefix.length);
  }}
  return String(m?.model_name||m?.custom_model_id||'');
}};
const normalizeUrl=value=>{{
  try{{
    const u=new URL(String(value||''));
    let path=u.pathname.replace(/\\/+$/,'');
    for(const suffix of ['/chat/completions','/messages','/models']){{
      if(path.endsWith(suffix)){{path=path.slice(0,-suffix.length);break;}}
    }}
    path=path.replace(/\\/+$/,'')||'/v1';
    return u.protocol.toLowerCase()+'//'+u.host.toLowerCase()+path;
  }}catch{{return '';}}
}};
const targetBase=normalizeUrl(input.baseUrl||input.endpoint);
const matchesTarget=(m,id)=>m?.config_source===3&&idOf(m)===id&&String(m?.provider||'')===provider&&normalizeUrl(m?.base_url)===targetBase;
const loadOnce=async()=>{{
  try{{
    const response=await request('model_list',{{}});
    if(response?.code===0) return Array.isArray(response?.data?.model_list)?response.data.model_list:[];
  }}catch{{}}
  return null;
}};
const load=async(attempts=120)=>{{
  for(let attempt=0;attempt<attempts;attempt++){{
    const current=await loadOnce();
    if(current!==null) return current;
    if(attempt+1<attempts) await sleep(500);
  }}
  throw new Error('model_list_failed');
}};
const waitForModel=async(id,attempts=60,expectedDisplay=null)=>{{
  let latest=[];
  for(let attempt=0;attempt<attempts;attempt++){{
    const current=await loadOnce();
    if(current!==null){{
      latest=current;
      if(current.some(m=>matchesTarget(m,id)&&(expectedDisplay===null||m?.display_name===expectedDisplay))) return current;
    }}
    if(attempt+1<attempts) await sleep(500);
  }}
  return latest;
}};
let models=await load();
// A second successful read avoids writing during the brief cold-start window
// where the renderer service exists but the authenticated model state is stale.
await sleep(750);
models=await load(30);
const result={{added:[],updated:[],kept:[],failed:[]}};
for(const model of input.models){{
  const matching=models.filter(m=>m?.config_source===3&&idOf(m)===model.id);
  let existing=matching.find(m=>matchesTarget(m,model.id));
  if(!existing&&matching.length){{result.kept.push(model.id);continue;}}
  if(existing&&!input.updateExisting){{result.kept.push(model.id);continue;}}
  const expectedDisplay=typeof model.displayName==='string'?model.displayName:null;
  const displayName=expectedDisplay??(existing?.display_name||model.id);
  const previousDetail=existing?.config_detail||{{}};
  const previousHyper=previousDetail?.model_hyper_params||{{}};
  const model_hyper_params={{...previousHyper,thinking_enable:previousHyper.thinking_enable??0}};
  if(Number.isInteger(model.maxInputTokens)) model_hyper_params.prompt_max_tokens=model.maxInputTokens;
  if(Number.isInteger(model.maxOutputTokens)) model_hyper_params.max_tokens=model.maxOutputTokens;
  const config_detail={{...previousDetail,multimodal:!!(model.supportsImages??input.supportsImages??previousDetail.multimodal??false),model_hyper_params,max_turn:previousDetail.max_turn??500}};
  let response=null;
  try{{
    if(existing){{
      response=await request('update_custom_model',{{action:'update',id:existing.custom_model_id,aK:input.apiKey,auth_type:0,base_url:input.endpoint,config_detail,display_name:displayName}});
    }}else{{
      response=await request('add_custom_model',{{model_name:model.id,provider,ak:input.apiKey,is_custom:true,auth_type:0,base_url:input.endpoint,config_detail,display_name:displayName}});
    }}
  }}catch{{}}
  const responseCode=Number(response?.code??-1);
  if(existing&&responseCode!==0){{
    // Presence alone cannot prove that an update was accepted, because the
    // record existed before the request. Never report a failed update as done.
    result.failed.push({{id:model.id,code:responseCode,stage:'update_request'}});
    continue;
  }}
  // Verify persisted state even when the request response is transient or lost.
  // This avoids reporting failure after the client has actually saved the model.
  models=await waitForModel(model.id,responseCode===0?60:30,expectedDisplay);
  if(!models.some(m=>matchesTarget(m,model.id)&&(expectedDisplay===null||m?.display_name===expectedDisplay))){{
    result.failed.push({{
      id:model.id,
      code:responseCode===0?-2:responseCode,
      stage:responseCode===0?'persistence_verify':'add_request'
    }});
    continue;
  }}
  (existing?result.updated:result.added).push(model.id);
}}
if(result.failed.length){{
  const failed=new Set(result.failed.map(x=>x.id));
  result.added=result.added.filter(x=>!failed.has(x));
  result.updated=result.updated.filter(x=>!failed.has(x));
}}
return result;
}})()"""


_RENDERER_READY_EXPRESSION = """(()=>({
  readyState:document.readyState,
  hasWorkbench:!!window.workbenchPromise||!!SOURCE
}))()""".replace("SOURCE", _WORKBENCH_SOURCE_EXPRESSION)


def _renderer_ready(cdp):
    """Check renderer readiness without touching model or credential state."""
    result = cdp.call("Runtime.evaluate", {
        "expression": _RENDERER_READY_EXPRESSION,
        "returnByValue": True,
    }, timeout=5)
    value = (result.get("result") or {}).get("value")
    return (isinstance(value, dict) and value.get("readyState") == "complete"
            and value.get("hasWorkbench") is True)


def _run_client_import(executable, payload, relaunch=True):
    port = _free_port()
    args = [str(executable), f"--remote-debugging-address={DEBUG_HOST}", f"--remote-debugging-port={port}"]
    process = subprocess.Popen(args)
    cdp = None
    launched = False
    try:
        websocket_url = _wait_target(port, time.monotonic() + 60)
        launched = True
        cdp = _CDP(websocket_url)
        consecutive_ready = 0
        for attempt in range(120):
            try:
                if _renderer_ready(cdp):
                    consecutive_ready += 1
                    if consecutive_ready >= 2:
                        break
                else:
                    consecutive_ready = 0
            except _CDPProtocolError as error:
                if error.code != -32000:
                    raise core.UserError("TRAE 工作台就绪检测失败，未执行导入。") from None
                consecutive_ready = 0
                cdp.close()
                cdp = None
                time.sleep(0.5)
                websocket_url = _wait_target(port, time.monotonic() + 15)
                cdp = _CDP(websocket_url)
                continue
            time.sleep(0.5)
        else:
            raise core.UserError("等待 TRAE 工作台稳定超时，未执行导入。")
        result = None
        for attempt in range(12):
            try:
                result = cdp.call("Runtime.evaluate", {
                    "expression": _expression(payload), "awaitPromise": True, "returnByValue": True,
                }, timeout=max(180, 90 + len(payload["models"]) * 60))
                break
            except _CDPProtocolError as error:
                if error.code != -32000 or attempt == 11:
                    raise core.UserError("TRAE 客户端拒绝了本机导入操作。") from None
                cdp.close()
                cdp = None
                time.sleep(0.5)
                websocket_url = _wait_target(port, time.monotonic() + 15)
                cdp = _CDP(websocket_url)
        if result is None:
            raise core.UserError("TRAE 客户端拒绝了本机导入操作。")
        remote = result.get("result", {})
        if remote.get("subtype") == "error" or result.get("exceptionDetails"):
            stage = _safe_runtime_stage(result)
            suffix = f"（阶段：{stage}）" if stage else ""
            if stage == "workbench_module_missing":
                raise core.UserError(f"{APP_NAME} 工作台入口识别失败{suffix}，未提交模型变更，请重试或检查客户端适配。")
            raise core.UserError(f"{APP_NAME} 内部导入接口不可用{suffix}，请重试或检查客户端适配。")
        value = remote.get("value")
        if not isinstance(value, dict):
            raise core.UserError(f"{APP_NAME} 未返回有效的导入结果。")
        return value
    finally:
        if cdp is not None:
            try:
                cdp.call("Browser.close", timeout=10)
            except Exception:
                pass
            cdp.close()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        # Whether import succeeds or fails, do not leave the temporary debug
        # instance behind. Restore the normal client after a renderer appeared.
        if launched and relaunch:
            subprocess.Popen([str(executable)])


def _safe_runtime_stage(result):
    """Return only an allow-listed adapter stage, never renderer exception text."""
    try:
        details = result.get("exceptionDetails") or {}
        values = [
            details.get("text"),
            (details.get("exception") or {}).get("description"),
            (result.get("result") or {}).get("description"),
        ]
        combined = "\n".join(value for value in values if isinstance(value, str))
    except (AttributeError, TypeError):
        return None
    stages = (
        "workbench_module_missing",
        "service_collection_missing",
        "connection_service_missing",
        "model_list_failed",
    )
    return next((stage for stage in stages if stage in combined), None)


def apply_plan(target, plan):
    target = target if isinstance(target, trae_adapter.Target) else trae_adapter.Target(SOLO_NAME, Path(target), SOLO_NAME)
    if plan.target.path.resolve() != target.path.resolve():
        raise core.UserError(f"{APP_NAME} 导入目标已变化，请重新预览。")
    ensure_solo_stopped(target)
    executable = discover_executable()
    payload = {
        "apiKey": plan.api_key,
        "baseUrl": plan.base_url,
        "protocol": plan.protocol,
        "provider": plan.provider,
        "endpoint": plan.endpoint,
        "supportsImages": plan.capabilities.get("supportsImages"),
        "updateExisting": bool(plan.update_existing),
        "models": [dict(id=mid, **plan.model_token_limits.get(mid, {}),
                        **plan.model_capabilities.get(mid, {}),
                        **({"displayName": plan.display_names[mid]} if mid in plan.display_names else {}))
                   for mid in plan.ids],
    }
    try:
        value = _run_client_import(executable, payload)
    except core.UserError:
        raise
    except (OSError, ValueError, KeyError, ConnectionError, socket.timeout):
        raise core.UserError(f"无法通过 {APP_NAME} 客户端完成持久化导入。") from None
    failed = value.get("failed")
    if failed:
        stage_labels = {
            "add_request": "新增请求",
            "update_request": "更新请求",
            "persistence_verify": "写入后验证",
        }
        details = "、".join(
            f"{item.get('id', '未知模型')}（{stage_labels.get(item.get('stage'), '客户端返回')}，代码 {item.get('code', -1)}）"
            for item in failed[:8]
        )
        suffix = " 等" if len(failed) > 8 else ""
        raise core.UserError(f"{APP_NAME} 未能导入：" + details + suffix)
    return ApplyResult(tuple(value.get("added", ())), tuple(value.get("updated", ())), tuple(value.get("kept", ())))


def ensure_solo_stopped(target):
    """Check only the TraeWork CN desktop executable, not helper command lines."""
    if os.name != "nt":
        raise core.UserError(f"{APP_NAME} 导入目前仅支持 Windows。")
    target = target if isinstance(target, trae_adapter.Target) else trae_adapter.Target(SOLO_NAME, Path(target), SOLO_NAME)
    script = (
        "$ErrorActionPreference='Stop'; "
        "@(Get-CimInstance Win32_Process | Where-Object { "
        "$_.Name -match '(?i)^TRAE SOLO CN(?:\\.exe)?$' -or "
        "$_.ExecutablePath -match '(?i)[\\\\/]TRAE SOLO CN(?:\\.exe)?$' "
        "}).Count"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0 or not result.stdout.strip().isdigit():
            raise core.UserError(f"无法确认 {APP_NAME} 是否已退出；为安全起见停止导入。")
        if int(result.stdout.strip()):
            raise core.UserError(f"请完全退出 {APP_NAME} 后再导入；工具不会强制结束应用。")
    except (OSError, subprocess.TimeoutExpired):
        raise core.UserError(f"无法检查 {APP_NAME} 运行状态，未导入任何模型。") from None
