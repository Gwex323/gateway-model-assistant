"""Conservative local-cache adapter for Trae plus reusable connection discovery.

Trae does not expose a supported external import file in the installed client.  This
adapter only edits the locally cached model-list rows after the client is closed,
keeps built-in models untouched, and creates an encrypted, account-bound backup.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import uuid
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone

import core

MAX_DB_BYTES = 64 * 1024 * 1024
MAX_VALUE_BYTES = 16 * 1024 * 1024
MODEL_MAP_SUFFIX = "AI.agent.model.model_list_map"
CUSTOM_PREFIX = "custom_openai_compatible//"
TRAE_WORK_CN_NAME = "TraeWork CN"
TRAE_WORK_CN_INTERNAL_NAME = "TRAE SOLO CN"


@dataclass(frozen=True)
class Target:
    name: str
    path: Path
    process_hint: str

    @property
    def exists(self):
        return self.path.is_file()


@dataclass(repr=False)
class Plan:
    target: Target = field(repr=False)
    original_digest: str = field(repr=False)
    payloads: dict[str, str] = field(repr=False, default_factory=dict)
    rows: list[tuple[str, str]] = field(default_factory=list)
    changed_maps: int = 0
    display_names: dict[str, str] = field(default_factory=dict)

    @property
    def changed(self):
        return bool(self.payloads)

    def summary(self):
        counts = []
        for action in ("新增", "更新", "保留已有", "保留其他连接", "保留内置同名", "无变化"):
            count = sum(row_action == action for _, row_action in self.rows)
            if count:
                counts.append(f"{action} {count}")
        maps = f"，覆盖 {self.changed_maps} 个 Trae 模式列表" if self.changed_maps else ""
        return "；".join(counts) + maps


def _safe_path(path):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink() or (hasattr(path.parent, "is_junction") and path.parent.is_junction()):
        raise core.UserError("为避免写错位置，不支持链接或目录联接形式的 Trae 数据库路径。")
    return path


def discover_targets(appdata=None):
    root = Path(appdata) if appdata else Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    return [
        Target("Trae", root / "Trae/User/globalStorage/state.vscdb", "Trae"),
        Target(TRAE_WORK_CN_NAME,
               root / f"{TRAE_WORK_CN_INTERNAL_NAME}/User/globalStorage/state.vscdb",
               TRAE_WORK_CN_INTERNAL_NAME),
    ]


def _connect(path, read_only=False):
    path = _safe_path(path)
    if not path.exists() or not path.is_file():
        raise core.UserError("未找到 Trae 的本地数据库，请先启动一次对应客户端。")
    try:
        size = path.stat().st_size
    except OSError:
        raise core.UserError("无法读取 Trae 本地数据库，请检查文件权限。") from None
    if size > MAX_DB_BYTES:
        raise core.UserError("Trae 本地数据库过大，已停止读取。")
    try:
        if read_only:
            uri = "file:" + str(path.resolve()).replace("\\", "/") + "?mode=ro"
            return sqlite3.connect(uri, uri=True, timeout=2)
        return sqlite3.connect(str(path), timeout=2)
    except (OSError, sqlite3.Error):
        raise core.UserError("无法打开 Trae 本地数据库；请确认客户端已完全退出。") from None


def _decode_value(value):
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            raise core.UserError("Trae 模型缓存包含无法识别的数据，未进行修改。") from None
    if not isinstance(value, str):
        raise core.UserError("Trae 模型缓存格式不是文本 JSON，未进行修改。")
    return value


def _read_maps(conn):
    try:
        rows = conn.execute("SELECT key, value FROM ItemTable WHERE key LIKE ?", ("%" + MODEL_MAP_SUFFIX,)).fetchall()
    except sqlite3.Error:
        raise core.UserError("Trae 本地数据库结构无法识别，未进行修改。") from None
    maps = []
    for key, value in rows:
        if not isinstance(key, str) or not key.endswith(MODEL_MAP_SUFFIX):
            continue
        raw = _decode_value(value)
        if len(raw.encode("utf-8")) > MAX_VALUE_BYTES:
            raise core.UserError("Trae 模型列表过大，已停止读取。")
        try:
            obj = json.loads(raw)
        except (ValueError, UnicodeError):
            raise core.UserError("Trae 模型列表不是有效 JSON，未进行修改。") from None
        if not isinstance(obj, dict) or any(not isinstance(v, list) for v in obj.values()):
            raise core.UserError("Trae 模型列表结构不是预期格式，未进行修改。")
        for mode, models in obj.items():
            for model in models:
                if not isinstance(model, dict):
                    raise core.UserError("Trae 模型列表包含无法识别的条目，未进行修改。")
        maps.append((key, obj, raw))
    if not maps:
        raise core.UserError("未找到 Trae 的模型列表缓存；请先在客户端打开一次模型选择器。")
    maps.sort(key=lambda item: item[0])
    return maps


def _digest(maps):
    h = hashlib.sha256()
    for key, _, raw in maps:
        encoded_key = key.encode("utf-8")
        encoded_raw = raw.encode("utf-8")
        h.update(len(encoded_key).to_bytes(4, "big"))
        h.update(encoded_key)
        h.update(len(encoded_raw).to_bytes(8, "big"))
        h.update(encoded_raw)
    return h.hexdigest()


def _model_id(model):
    name = model.get("name")
    if isinstance(name, str) and name.startswith(CUSTOM_PREFIX):
        return name[len(CUSTOM_PREFIX):]
    if model.get("config_source") == 3 and isinstance(model.get("custom_model_id"), str) and model["custom_model_id"].strip():
        return model["custom_model_id"]
    return None


def _is_custom_openai(model):
    return model.get("config_source") == 3 and (
        model.get("provider") == "custom_openai_compatible" or
        isinstance(model.get("name"), str) and model.get("name", "").startswith(CUSTOM_PREFIX)
    )


def _find_exemplar(maps):
    # Prefer the exact OpenAI-compatible shape, then any user-created Trae model
    # so newer client versions retain their extra bookkeeping fields.
    fallback = None
    for _, obj, _ in maps:
        for models in obj.values():
            for model in models:
                if model.get("config_source") == 3:
                    fallback = fallback or copy.deepcopy(model)
                    if _is_custom_openai(model):
                        return copy.deepcopy(model)
    if fallback is not None:
        return fallback
    return {
        "name": CUSTOM_PREFIX,
        "multimodal": False,
        "is_default": False,
        "custom_config": "{}",
        "display_name": "",
        "model_type": "chat_model",
        "builder": None,
        "is_preset": False,
        "client_connect": True,
        "provider": "custom_openai_compatible",
        "icon": None,
        "ak": "",
        "base_url": "",
        "is_custom_base_url": True,
        "status": True,
        "custom_model_id": "",
        "config_source": 3,
        "selectable": True,
        "sk": "",
        "auth_type": 0,
        "region": None,
        "max_turn": 200,
    }


def _endpoint(base_url, allow_private_http=False):
    url = core.normalize_url(base_url, allow_private_http=allow_private_http)
    return url


def validate_display_names(values, ids):
    """Validate optional labels; blank means preserve existing / use ID for new."""
    if values is None:
        return {}
    allowed = set(ids)
    if not isinstance(values, dict) or any(mid not in allowed for mid in values):
        raise core.UserError('显示名称必须对应本次选择的模型。')
    result = {}
    for mid, value in values.items():
        if not isinstance(value, str):
            raise core.UserError('显示名称必须是文本。')
        if any(unicodedata.category(char) in ('Cc', 'Cs') for char in value):
            raise core.UserError('显示名称不能包含换行或控制字符。')
        name = value.strip()
        if len(name) > 128:
            raise core.UserError('助手中的显示名称最多 128 个字符。')
        if name:
            result[mid] = name
    return result


def _make_model(exemplar, model_id, endpoint, api_key, multimodal=False,
                token_limits=None, preserve_unspecified_limits=False, display_name=None):
    model = copy.deepcopy(exemplar)
    model.update({
        "name": CUSTOM_PREFIX + model_id,
        "display_name": display_name if display_name is not None else (
            exemplar.get("display_name") or model_id if preserve_unspecified_limits else model_id),
        "custom_model_id": model_id,
        "provider": "custom_openai_compatible",
        "ak": api_key,
        "sk": "",
        "base_url": endpoint,
        "is_custom_base_url": True,
        "status": True,
        "selectable": True,
        "config_source": 3,
        "auth_type": 0,
        "custom_model_type": model.get("custom_model_type", ""),
        "model_type": "chat_model",
        "client_connect": True,
        "multimodal": bool(multimodal),
    })
    field_map = {
        "maxInputTokens": "prompt_max_tokens",
        "maxOutputTokens": "max_tokens",
    }
    for source, target in field_map.items():
        if source in (token_limits or {}):
            model[target] = token_limits[source]
        elif not preserve_unspecified_limits:
            model.pop(target, None)
    return model


def _same_model(a, b):
    return a == b


def make_plan(target, base_url, api_key, ids, capabilities=None, update_existing=False,
              allow_private_http=False, token_limits=None, token_limits_by_model=None,
              display_names_by_model=None, capabilities_by_model=None):
    target = target if isinstance(target, Target) else Target("Trae", Path(target), "Trae")
    url = core.normalize_url(base_url, allow_private_http=allow_private_http)
    key = core.check_key(api_key)
    if not ids or len(ids) > 10000 or any(not core.valid_id(mid) for mid in ids):
        raise core.UserError("请至少选择一个有效模型；每批最多 10000 个。")
    flags = capabilities or {}
    per_model = core.validate_model_capabilities(capabilities_by_model)
    display_names = validate_display_names(display_names_by_model, ids)
    default_limits = core.validate_token_limits(token_limits)
    model_limits = core.validate_model_token_limits(token_limits_by_model)
    if any(k not in ("supportsImages", "supportsToolCall", "supportsReasoning") or type(v) is not bool for k, v in flags.items()):
        raise core.UserError("模型能力设置不合法。")
    path = _safe_path(target.path)
    conn = _connect(path, read_only=True)
    try:
        maps = _read_maps(conn)
    finally:
        conn.close()
    exemplar = _find_exemplar(maps)
    endpoint = _endpoint(url, allow_private_http=allow_private_http)
    normalized_ids = list(dict.fromkeys(ids))
    changed = {key_name: copy.deepcopy(obj) for key_name, obj, _ in maps}
    rows = []
    for mid in normalized_ids:
        model_flags = core.model_capabilities(mid, flags, per_model)
        multimodal = bool(model_flags.get("supportsImages", False))
        limits = model_limits.get(mid, default_limits)
        custom_same_any = False
        custom_other_any = False
        native_any = False
        for _, obj, _ in maps:
            for models in obj.values():
                for model in models:
                    if _model_id(model) == mid:
                        if _is_custom_openai(model):
                            if core.same_gateway_url(model.get("base_url"), url):
                                custom_same_any = True
                            else:
                                custom_other_any = True
                        else:
                            native_any = True
        if custom_same_any and not update_existing:
            rows.append((mid, "保留已有"))
            continue
        if not custom_same_any and custom_other_any:
            rows.append((mid, "保留其他连接"))
            continue
        if not custom_same_any and native_any:
            rows.append((mid, "保留内置同名"))
            continue
        changed_this = False
        for key_name, _, _ in maps:
            obj = changed[key_name]
            for mode, models in obj.items():
                found = next((
                    m for m in models
                    if _is_custom_openai(m) and _model_id(m) == mid
                    and core.same_gateway_url(m.get("base_url"), url)
                ), None)
                if found is None:
                    conflict = next((m for m in models if _model_id(m) == mid), None)
                    if conflict is None:
                        # Add to every non-conflicting mode list.
                        models.append(_make_model(
                            exemplar, mid, endpoint, key, multimodal, limits,
                            display_name=display_names.get(mid),
                        ))
                        changed_this = True
                elif update_existing:
                    replacement = _make_model(
                        found, mid, endpoint, key, model_flags.get("supportsImages", found.get("multimodal", False)), limits,
                        preserve_unspecified_limits=True,
                        display_name=display_names.get(mid),
                    )
                    if not _same_model(found, replacement):
                        index = models.index(found)
                        models[index] = replacement
                        changed_this = True
        if changed_this:
            rows.append((mid, "更新" if custom_same_any else "新增"))
        else:
            rows.append((mid, "无变化" if custom_same_any else "保留已有"))
    payloads = {}
    changed_maps = 0
    for key_name, obj, raw in maps:
        if changed[key_name] == obj:
            continue
        new_raw = json.dumps(changed[key_name], ensure_ascii=False, separators=(",", ":"))
        if len(new_raw.encode("utf-8")) > MAX_VALUE_BYTES:
            raise core.UserError("导入后的 Trae 模型列表过大，已停止写入。")
        payloads[key_name] = new_raw
        changed_maps += 1
    return Plan(target, _digest(maps), payloads, rows, changed_maps, dict(display_names))


def _process_script(process_hint):
    hint = process_hint.replace("'", "''")
    name_pattern = re.escape(hint) + r"(?:\.exe)?"
    path_pattern = r"[\\/]" + re.escape(hint) + r"(?:[\\/]|\.exe)"
    return (
        "$ErrorActionPreference='Stop'; "
        "@(Get-CimInstance Win32_Process | Where-Object { "
        f"$_.Name -match '(?i)^{name_pattern}$' -or "
        f"$_.ExecutablePath -match '(?i){path_pattern}' -or "
        f"$_.CommandLine -match '(?i){path_pattern}' "
        "}).Count"
    )


def ensure_trae_stopped(target):
    if os.name != "nt":
        raise core.UserError("Trae 直接导入与恢复目前仅支持 Windows。")
    target = target if isinstance(target, Target) else Target("Trae", Path(target), "Trae")
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _process_script(target.process_hint)],
                                capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode != 0 or not result.stdout.strip().isdigit():
            raise core.UserError("无法确认 Trae 是否已退出；为安全起见停止写入。")
        if int(result.stdout.strip()):
            raise core.UserError(f"请完全退出 {target.name}（包括后台进程）后再导入或恢复；工具不会强制结束应用。")
    except (OSError, subprocess.TimeoutExpired):
        raise core.UserError("无法检查 Trae 运行状态，未写入任何配置。") from None


def _file_bytes(path):
    path = Path(path)
    if not path.exists():
        return None
    try:
        if path.stat().st_size > MAX_DB_BYTES:
            raise core.UserError("Trae 数据库文件过大，已停止备份。")
        return path.read_bytes()
    except OSError:
        raise core.UserError("无法读取 Trae 数据库，未进行写入。") from None


def _backup_folder(path):
    folder = Path(path).parent / "gateway-model-assistant-backups"
    if folder.is_symlink() or (hasattr(folder, "is_junction") and folder.is_junction()):
        raise core.UserError("Trae 备份目录不能是链接或目录联接。")
    folder.mkdir(exist_ok=True)
    return folder


def _make_backup(path):
    path = _safe_path(path)
    files = {}
    for suffix in ("", "-wal", "-shm"):
        item = Path(str(path) + suffix)
        data = _file_bytes(item)
        files[item.name] = None if data is None else __import__("base64").b64encode(data).decode("ascii")
    body = json.dumps({"version": 1, "target": str(path.resolve()), "files": files}, ensure_ascii=False).encode("utf-8")
    encrypted = core.dpapi(body)
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:10] + ".traebackup"
    result = _backup_folder(path) / name
    core.atomic_write(result, b"TRGA1\n" + __import__("base64").b64encode(encrypted))
    return result


def _read_backup(backup_path, target_path):
    import base64
    try:
        raw = Path(backup_path).read_bytes()
        if len(raw) > 12 * MAX_DB_BYTES or not raw.startswith(b"TRGA1\n"):
            raise ValueError()
        obj = json.loads(core.dpapi(base64.b64decode(raw[6:], validate=True), decrypt=True))
        if obj.get("version") != 1 or Path(obj.get("target", "")).resolve() != Path(target_path).resolve():
            raise ValueError()
        files = obj.get("files")
        if not isinstance(files, dict) or set(files) != {Path(str(target_path) + s).name for s in ("", "-wal", "-shm")}:
            raise ValueError()
        decoded = {}
        for name, encoded in files.items():
            if encoded is None:
                decoded[name] = None
            else:
                value = base64.b64decode(encoded, validate=True)
                if len(value) > MAX_DB_BYTES:
                    raise ValueError()
                decoded[name] = value
        return decoded
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        raise core.UserError("这不是适用于当前 Trae 数据库的有效加密备份。") from None


def _restore_files(path, files):
    for suffix in ("", "-wal", "-shm"):
        item = Path(str(path) + suffix)
        data = files[item.name]
        if data is None:
            try:
                item.unlink()
            except FileNotFoundError:
                pass
        else:
            core.atomic_write(item, data)


def apply_plan(target, plan):
    target = target if isinstance(target, Target) else Target("Trae", Path(target), "Trae")
    if plan.target.path.resolve() != target.path.resolve():
        raise core.UserError("Trae 导入目标已变化，请重新预览。")
    ensure_trae_stopped(target)
    if not plan.changed:
        return None
    path = _safe_path(target.path)
    conn = None
    backup = None
    try:
        conn = _connect(path)
        conn.isolation_level = None
        conn.execute("PRAGMA busy_timeout=1500")
        conn.execute("BEGIN IMMEDIATE")
        current = _digest(_read_maps(conn))
        if current != plan.original_digest:
            raise core.UserError("Trae 配置在预览后发生变化，请重新预览，避免覆盖其他修改。")
        backup = _make_backup(path)
        for key_name, payload in plan.payloads.items():
            cursor = conn.execute("UPDATE ItemTable SET value=? WHERE key=?", (payload, key_name))
            if cursor.rowcount != 1:
                raise core.UserError("Trae 模型列表在写入过程中发生变化，已停止。")
        conn.commit()
        ensure_trae_stopped(target)
        return backup
    except core.UserError:
        if conn is not None:
            try: conn.rollback()
            except sqlite3.Error: pass
        raise
    except (sqlite3.Error, OSError):
        if conn is not None:
            try: conn.rollback()
            except sqlite3.Error: pass
        raise core.UserError("写入 Trae 本地模型缓存失败，未显示原始错误。") from None
    finally:
        if conn is not None:
            conn.close()


def restore_backup_file(backup_path):
    backup_path = Path(backup_path)
    try:
        raw = backup_path.read_bytes()
    except OSError:
        raise core.UserError("无法读取 Trae 加密备份。") from None
    # Decode the target path from the protected payload before changing anything.
    import base64
    try:
        if not raw.startswith(b"TRGA1\n"):
            raise ValueError()
        obj = json.loads(core.dpapi(base64.b64decode(raw[6:], validate=True), decrypt=True))
        target_path = Path(obj["target"])
    except (ValueError, KeyError, TypeError, OSError, UnicodeError):
        raise core.UserError("这不是有效的 Trae 加密备份。") from None
    target = next((item for item in discover_targets() if item.path.resolve() == target_path.resolve()), None)
    if target is None:
        raise core.UserError("这份备份不属于当前用户已发现的 Trae 数据库。")
    files = _read_backup(backup_path, target_path)
    ensure_trae_stopped(target)
    safety = _make_backup(target_path) if target_path.exists() else None
    _restore_files(target_path, files)
    return safety
