"""config_manager.py — Safe structural editing of config.toml using tomlkit."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Tuple

import tomlkit

from history_fixer import backup_file

CODEX_HOME = Path.home() / ".codex"
CONFIG_PATH = CODEX_HOME / "config.toml"
STATE_PATH = Path.home() / ".codex-toolkit" / "state.json"
ENV_PROXY_SENTINEL = "系统环境代理"


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
    # Migrate older state without the explicit environment option while
    # preserving user-defined entries and labels.
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
    state = _load_state()
    managed_base = state.get("managed_proxy_base_url")
    snapshot_matches = None if not managed_base else base_url.rstrip("/") == str(managed_base).rstrip("/")

    result.update({
        "parse_ok": True,
        "provider": provider,
        "active": provider == "openai-idfix",
        "base_url": base_url or None,
        "managed_snapshot_matches": snapshot_matches,
        "supports_websockets": bool(block.get("supports_websockets", True)) if block else True,
        "transport_mode": get_transport_mode(),
    })
    if port is None:
        result["active_for_port"] = result["active"]
    else:
        expected = f"http://127.0.0.1:{int(port)}/v1"
        result["active_for_port"] = result["active"] and base_url.rstrip("/") == expected.rstrip("/")
    return result


DEFAULT_TRANSPORT_MODE = "auto"
VALID_TRANSPORT_MODES = ("auto", "websocket", "http")


def get_transport_mode() -> str:
    """Return 'auto', 'websocket', or 'http'."""
    state = _load_state()
    mode = str(state.get("transport_mode", DEFAULT_TRANSPORT_MODE)).lower()
    return mode if mode in VALID_TRANSPORT_MODES else DEFAULT_TRANSPORT_MODE


def get_circuit_breaker_config() -> dict[str, Any]:
    state = _load_state()
    cfg = state.get("circuit_breaker") or {}
    cd_min = max(1, int(cfg.get("cooldown_minutes", 15)))
    cd_sec = max(1, int(cfg.get("cooldown_seconds", cd_min * 60)))
    action = str(cfg.get("action_mode", "auto_switch"))
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "action_mode": action if action in {"auto_switch", "notify_only"} else "auto_switch",
        "cooldown_minutes": cd_min,
        "cooldown_seconds": cd_sec,
        "failure_threshold": max(1, int(cfg.get("failure_threshold", 3))),
    }


def save_circuit_breaker_config(
    cfg: dict[str, Any] | int | None = None,
    cooldown_seconds: int | None = None,
    *,
    failure_threshold: int | None = None,
    enabled: bool | None = None,
    action_mode: str | None = None,
    cooldown_minutes: int | None = None,
) -> None:
    state = _load_state()
    current = state.get("circuit_breaker") or {}

    if isinstance(cfg, dict):
        d_enabled = cfg.get("enabled", current.get("enabled", True))
        d_action = cfg.get("action_mode", current.get("action_mode", "auto_switch"))
        d_thresh = cfg.get("failure_threshold", current.get("failure_threshold", 3))
        d_sec = cfg.get("cooldown_seconds")
        d_min = cfg.get("cooldown_minutes")
        if d_sec is not None:
            cd_sec = int(d_sec)
            cd_min = max(1, (cd_sec + 59) // 60)
        elif d_min is not None:
            cd_min = int(d_min)
            cd_sec = cd_min * 60
        else:
            cd_min = int(current.get("cooldown_minutes", 15))
            cd_sec = int(current.get("cooldown_seconds", cd_min * 60))
    elif isinstance(cfg, int):
        d_thresh = cfg
        cd_sec = int(cooldown_seconds or 900)
        cd_min = max(1, (cd_sec + 59) // 60)
        d_enabled = current.get("enabled", True)
        d_action = current.get("action_mode", "auto_switch")
    else:
        d_thresh = failure_threshold if failure_threshold is not None else current.get("failure_threshold", 3)
        d_enabled = enabled if enabled is not None else current.get("enabled", True)
        d_action = action_mode if action_mode is not None else current.get("action_mode", "auto_switch")
        if cooldown_seconds is not None:
            cd_sec = int(cooldown_seconds)
            cd_min = max(1, (cd_sec + 59) // 60)
        elif cooldown_minutes is not None:
            cd_min = int(cooldown_minutes)
            cd_sec = cd_min * 60
        else:
            cd_min = int(current.get("cooldown_minutes", 15))
            cd_sec = int(current.get("cooldown_seconds", cd_min * 60))

    cd_sec = max(1, int(cd_sec))
    cd_min = max(1, int(cd_min))
    state["circuit_breaker"] = {
        "enabled": bool(d_enabled),
        "action_mode": "auto_switch" if d_action == "auto_switch" else "notify_only",
        "cooldown_minutes": cd_min,
        "cooldown_seconds": cd_sec,
        "failure_threshold": max(1, int(d_thresh)),
    }
    _save_state(state)


def set_transport_mode(mode: str, port: int | None = None) -> Tuple[bool, str]:
    """Persist transport mode and safely update the managed provider when present."""
    mode = str(mode).lower().strip()
    if mode not in VALID_TRANSPORT_MODES:
        return False, f"Invalid transport mode: {mode!r}. Expected one of {VALID_TRANSPORT_MODES}"

    ws_enabled = mode != "http"
    state = _load_state()

    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                doc = tomlkit.load(f)
            providers = doc.get("model_providers", {})
            block = providers.get("openai-idfix") if providers else None
            if block is not None and block.get("supports_websockets") != ws_enabled:
                backup_file(CONFIG_PATH)
                block["supports_websockets"] = ws_enabled
                temp_path = CONFIG_PATH.with_suffix(f".tmp.{time.time()}")
                try:
                    with open(temp_path, "w", encoding="utf-8") as f:
                        tomlkit.dump(doc, f)
                        f.flush()
                        os.fsync(f.fileno())
                    with open(temp_path, "r", encoding="utf-8") as f:
                        tomlkit.load(f)
                    os.replace(temp_path, CONFIG_PATH)
                finally:
                    if temp_path.exists():
                        try:
                            temp_path.unlink()
                        except OSError:
                            pass
                state["managed_proxy_ws"] = ws_enabled
        except Exception as exc:
            return False, f"保存传输模式配置失败: {exc}"

    state["transport_mode"] = mode
    try:
        _save_state(state)
    except Exception as exc:
        return False, f"保存传输模式状态失败: {exc}"
    return True, f"传输模式已更新为 {mode} (supports_websockets={ws_enabled})。"

def update_managed_ws_support(ws_enabled: bool) -> Tuple[bool, str]:
    """Update supports_websockets in config.toml without altering provider or base_url."""
    if not CONFIG_PATH.exists():
        return False, "Config file not found."
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            doc = tomlkit.load(f)
        providers = doc.get("model_providers", {})
        if "openai-idfix" not in providers:
            return False, "openai-idfix provider not found in config.toml"

        block = providers["openai-idfix"]
        if block.get("supports_websockets") != bool(ws_enabled):
            backup_file(CONFIG_PATH)
        block["supports_websockets"] = bool(ws_enabled)

        state = _load_state()
        state["managed_proxy_ws"] = bool(ws_enabled)

        temp_path = CONFIG_PATH.with_suffix(f".tmp.{time.time()}")
        with open(temp_path, "w", encoding="utf-8") as f:
            tomlkit.dump(doc, f)
            f.flush()
            os.fsync(f.fileno())
        with open(temp_path, "r", encoding="utf-8") as f:
            tomlkit.load(f)
        os.replace(temp_path, CONFIG_PATH)
        _save_state(state)
        return True, f"Updated supports_websockets to {ws_enabled}"
    except Exception as exc:
        return False, f"Failed to update supports_websockets: {exc}"


def enable_proxy_config(port: int, ws_enabled: bool) -> Tuple[bool, str]:
    if not CONFIG_PATH.exists():
        return False, f"Config file not found: {CONFIG_PATH}"

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

    t_mode = get_transport_mode()
    if t_mode == "http":
        ws_enabled = False
    elif t_mode == "websocket":
        ws_enabled = True

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
        block["supports_websockets"] = bool(ws_enabled)
        providers.add("openai-idfix", block)
    else:
        block = providers["openai-idfix"]
        block["base_url"] = managed_base
        block["supports_websockets"] = bool(ws_enabled)

    state["managed_proxy_base_url"] = managed_base
    state["managed_proxy_ws"] = bool(ws_enabled)
    state["managed_at"] = time.time()

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
        return True, "Config successfully updated."
    except Exception as exc:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        return False, f"Failed to save config: {exc}"


def disable_proxy_config(*, force: bool = False) -> Tuple[bool, str]:
    """Restore the previous provider without overwriting external config edits.

    By default restoration is allowed only while ``model_provider`` is still
    ``openai-idfix`` and (when known) its base URL matches the last value written
    by this Toolkit.  ``force=True`` exists for CLI/recovery scenarios only.
    """
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
