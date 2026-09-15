"""config_manager.py — Safe structural editing of config.toml using tomlkit."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Tuple

import tomlkit

from history_fixer import backup_file

CODEX_HOME = Path.home() / ".codex"
CONFIG_PATH = CODEX_HOME / "config.toml"
STATE_PATH = Path.home() / ".codex-toolkit" / "state.json"
ENV_PROXY_SENTINEL = "系统环境代理"

TRANSPORT_AUTO = "auto"
TRANSPORT_WEBSOCKET = "websocket"
TRANSPORT_HTTP = "http"
VALID_TRANSPORT_MODES = {TRANSPORT_AUTO, TRANSPORT_WEBSOCKET, TRANSPORT_HTTP}

DEFAULT_TOOLKIT_SETTINGS = {
    "transport_mode": TRANSPORT_AUTO,
    "auto_circuit_breaker": False,
    "auto_start_proxy": False,
    "auto_apply_config": False,
    "auto_launch_codex": False,
    "stop_proxy_on_codex_exit": False,
}


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = STATE_PATH.with_suffix(f".tmp.{time.time()}")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(state, ensure_ascii=False, indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, STATE_PATH)
    except Exception:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise


def normalize_transport_mode(value: str | bool | None) -> str:
    """Normalize old boolean WS settings and new transport-mode strings."""
    if isinstance(value, bool):
        return TRANSPORT_WEBSOCKET if value else TRANSPORT_HTTP
    mode = str(value or TRANSPORT_AUTO).strip().lower()
    return mode if mode in VALID_TRANSPORT_MODES else TRANSPORT_AUTO


def transport_uses_websockets(value: str | bool | None) -> bool:
    return normalize_transport_mode(value) != TRANSPORT_HTTP


def load_toolkit_settings() -> dict:
    state = _load_state()
    saved = state.get("toolkit_settings") or {}
    result = dict(DEFAULT_TOOLKIT_SETTINGS)
    if isinstance(saved, dict):
        result.update({k: saved[k] for k in result if k in saved})
    # Migrate the old managed WS flag if there is no explicit transport setting.
    if "transport_mode" not in saved and "managed_proxy_ws" in state:
        result["transport_mode"] = (
            TRANSPORT_WEBSOCKET if bool(state.get("managed_proxy_ws")) else TRANSPORT_HTTP
        )
    result["transport_mode"] = normalize_transport_mode(result.get("transport_mode"))
    for key in (
        "auto_circuit_breaker",
        "auto_start_proxy",
        "auto_apply_config",
        "auto_launch_codex",
        "stop_proxy_on_codex_exit",
    ):
        result[key] = bool(result.get(key, False))
    return result


def save_toolkit_settings(settings: dict | None = None, **updates) -> dict:
    current = load_toolkit_settings()
    if isinstance(settings, dict):
        current.update({k: settings[k] for k in current if k in settings})
    current.update({k: v for k, v in updates.items() if k in current})
    current["transport_mode"] = normalize_transport_mode(current.get("transport_mode"))
    for key in (
        "auto_circuit_breaker",
        "auto_start_proxy",
        "auto_apply_config",
        "auto_launch_codex",
        "stop_proxy_on_codex_exit",
    ):
        current[key] = bool(current.get(key, False))
    state = _load_state()
    state["toolkit_settings"] = current
    _save_state(state)
    return dict(current)


def load_upstreams() -> Tuple[dict[str, str], str]:
    state = _load_state()
    upstreams = state.get("upstreams", {
        "官方直连": "https://chatgpt.com/backend-api/codex"
    })
    selected = state.get("selected_upstream", "官方直连")
    if selected not in upstreams:
        selected = list(upstreams.keys())[0] if upstreams else ""
    return upstreams, selected


def save_upstreams(upstreams: dict[str, str], selected: str) -> None:
    state = _load_state()
    state["upstreams"] = upstreams
    state["selected_upstream"] = selected
    _save_state(state)


def load_proxies() -> Tuple[dict[str, str], str]:
    state = _load_state()
    defaults = {
        "": "无代理（真正直连）",
        ENV_PROXY_SENTINEL: "跟随系统环境代理",
        "http://127.0.0.1:7897": "Clash Verge Rev (常见 Mixed)",
        "http://127.0.0.1:7890": "Clash / Mihomo (常见 Mixed)",
        "http://127.0.0.1:10809": "v2rayN (常见 HTTP)",
        "http://127.0.0.1:2081": "NekoRay (常见 HTTP)",
    }
    proxies = state.get("proxies") or defaults
    proxies = dict(proxies)
    proxies.setdefault("", "无代理（真正直连）")
    proxies.setdefault(ENV_PROXY_SENTINEL, "跟随系统环境代理")
    selected = state.get("selected_proxy", "")
    if selected not in proxies:
        if selected:
            proxies[selected] = "自定义"
        else:
            selected = ""
    return proxies, selected


def save_proxies(proxies: dict[str, str], selected: str) -> None:
    state = _load_state()
    state["proxies"] = proxies
    state["selected_proxy"] = selected
    _save_state(state)


def get_proxy_config_status(port: int | None = None) -> dict:
    """Read whether Codex currently points at the local id-fix provider."""
    result = {
        "config_exists": CONFIG_PATH.exists(),
        "parse_ok": False,
        "provider": None,
        "active": False,
        "base_url": None,
        "supports_websockets": None,
        "transport_mode": None,
        "active_for_port": False,
        "managed_snapshot_matches": None,
    }
    if not CONFIG_PATH.exists():
        return result
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            doc = tomlkit.load(f)
    except Exception as exc:
        result["error"] = str(exc)
        return result

    provider = str(doc.get("model_provider", "openai"))
    providers = doc.get("model_providers", {})
    block = providers.get("openai-idfix", {}) if providers else {}
    base_url = str(block.get("base_url", "")) if block else ""
    ws_value = bool(block.get("supports_websockets", True)) if block else None
    state = _load_state()
    managed_base = state.get("managed_proxy_base_url")
    snapshot_matches = None if not managed_base else base_url.rstrip("/") == str(managed_base).rstrip("/")
    settings = load_toolkit_settings()
    transport_mode = normalize_transport_mode(
        state.get("managed_transport_mode")
        or settings.get("transport_mode")
        or (TRANSPORT_WEBSOCKET if ws_value else TRANSPORT_HTTP)
    )

    result.update({
        "parse_ok": True,
        "provider": provider,
        "active": provider == "openai-idfix",
        "base_url": base_url or None,
        "supports_websockets": ws_value,
        "transport_mode": transport_mode,
        "managed_snapshot_matches": snapshot_matches,
    })
    if port is None:
        result["active_for_port"] = result["active"]
    else:
        expected = f"http://127.0.0.1:{int(port)}/v1"
        result["active_for_port"] = result["active"] and base_url.rstrip("/") == expected.rstrip("/")
    return result


def enable_proxy_config(port: int, ws_enabled: bool | str = TRANSPORT_AUTO) -> Tuple[bool, str]:
    """Enable the managed local provider.

    ``ws_enabled`` remains backward compatible with the historic boolean API.
    New callers should pass ``auto``, ``websocket`` or ``http``.
    """
    if not CONFIG_PATH.exists():
        return False, f"Config file not found: {CONFIG_PATH}"

    transport_mode = normalize_transport_mode(ws_enabled)
    websocket_enabled = transport_uses_websockets(transport_mode)

    backup_file(CONFIG_PATH)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        try:
            doc = tomlkit.load(f)
        except Exception as exc:
            return False, f"Failed to parse config.toml: {exc}"

    current_provider = str(doc.get("model_provider", "openai"))
    state = _load_state()
    if current_provider != "openai-idfix":
        state["previous_model_provider"] = current_provider

    managed_base = f"http://127.0.0.1:{int(port)}/v1"
    doc["model_provider"] = "openai-idfix"

    if "model_providers" not in doc:
        doc.add("model_providers", tomlkit.table())
    providers = doc["model_providers"]
    if "openai-idfix" not in providers:
        block = tomlkit.table()
        block["name"] = "OpenAI (ID-fix proxy)"
        block["base_url"] = managed_base
        block["wire_api"] = "responses"
        block["requires_openai_auth"] = True
        block["supports_websockets"] = websocket_enabled
        providers.add("openai-idfix", block)
    else:
        block = providers["openai-idfix"]
        block["base_url"] = managed_base
        block["supports_websockets"] = websocket_enabled

    state["managed_proxy_base_url"] = managed_base
    state["managed_proxy_ws"] = websocket_enabled
    state["managed_transport_mode"] = transport_mode
    state["managed_at"] = time.time()
    settings = load_toolkit_settings()
    settings["transport_mode"] = transport_mode
    state["toolkit_settings"] = settings

    temp_path = CONFIG_PATH.with_suffix(f".tmp.{time.time()}")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            tomlkit.dump(doc, f)
            f.flush()
            os.fsync(f.fileno())
        with open(temp_path, "r", encoding="utf-8") as f:
            tomlkit.load(f)
        os.replace(temp_path, CONFIG_PATH)
        _save_state(state)
        return True, f"Config successfully updated ({transport_mode})."
    except Exception as exc:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        return False, f"Failed to save config: {exc}"


def disable_proxy_config(*, force: bool = False) -> Tuple[bool, str]:
    """Restore the previous provider without overwriting external config edits."""
    if not CONFIG_PATH.exists():
        return False, "Config file not found."

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        try:
            doc = tomlkit.load(f)
        except Exception as exc:
            return False, f"Failed to parse config.toml: {exc}"

    state = _load_state()
    current_provider = str(doc.get("model_provider", "openai"))
    providers = doc.get("model_providers", {})
    block = providers.get("openai-idfix", {}) if providers else {}
    current_base = str(block.get("base_url", "")) if block else ""
    managed_base = state.get("managed_proxy_base_url")

    if not force and current_provider != "openai-idfix":
        return False, (
            f"配置已被外部修改：当前 provider 为 {current_provider!r}，不是 openai-idfix。"
            " 为避免覆盖用户修改，Toolkit 未写入 config.toml。"
        )
    if not force and managed_base and current_base.rstrip("/") != str(managed_base).rstrip("/"):
        return False, (
            "配置已被外部修改：openai-idfix 的 base_url 与 Toolkit 最后写入值不同。"
            " 为避免覆盖用户修改，Toolkit 未恢复配置。"
        )

    backup_file(CONFIG_PATH)
    prev_provider = str(state.get("previous_model_provider", "openai"))
    if prev_provider != "openai":
        if prev_provider not in providers or prev_provider == "openai-idfix":
            prev_provider = "openai"
    doc["model_provider"] = prev_provider

    temp_path = CONFIG_PATH.with_suffix(f".tmp.{time.time()}")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            tomlkit.dump(doc, f)
            f.flush()
            os.fsync(f.fileno())
        with open(temp_path, "r", encoding="utf-8") as f:
            tomlkit.load(f)
        os.replace(temp_path, CONFIG_PATH)
        state.pop("managed_proxy_base_url", None)
        state.pop("managed_proxy_ws", None)
        state.pop("managed_transport_mode", None)
        state.pop("managed_at", None)
        _save_state(state)
        return True, f"Restored default provider: {prev_provider}"
    except Exception as exc:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        return False, f"Failed to restore config: {exc}"
