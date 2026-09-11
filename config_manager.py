"""
config_manager.py — Safe structural editing of config.toml using tomlkit.
"""

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

def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save_state(state: dict):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    import os, time
    temp_path = STATE_PATH.with_suffix(f".tmp.{time.time()}")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(state, indent=2))
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

def enable_proxy_config(port: int, ws_enabled: bool) -> Tuple[bool, str]:
    if not CONFIG_PATH.exists():
        return False, f"Config file not found: {CONFIG_PATH}"
        
    backup_file(CONFIG_PATH)
    
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        try:
            doc = tomlkit.load(f)
        except Exception as e:
            return False, f"Failed to parse config.toml: {e}"
            
    # Save previous provider if not already idfix
    current_provider = doc.get("model_provider", "openai")
    if current_provider != "openai-idfix":
        state = _load_state()
        state["previous_model_provider"] = str(current_provider)
        _save_state(state)
        
    doc["model_provider"] = "openai-idfix"
    
    if "model_providers" not in doc:
        doc.add("model_providers", tomlkit.table())
        
    providers = doc["model_providers"]
    
    if "openai-idfix" not in providers:
        block = tomlkit.table()
        block["name"] = "OpenAI (ID-fix proxy)"
        block["base_url"] = f"http://127.0.0.1:{port}/v1"
        block["wire_api"] = "responses"
        block["requires_openai_auth"] = True
        block["supports_websockets"] = ws_enabled
        providers.add("openai-idfix", block)
    else:
        block = providers["openai-idfix"]
        block["base_url"] = f"http://127.0.0.1:{port}/v1"
        block["supports_websockets"] = ws_enabled
        
    try:
        # Atomic write
        temp_path = CONFIG_PATH.with_suffix(f".tmp.{time.time()}")
        with open(temp_path, "w", encoding="utf-8") as f:
            tomlkit.dump(doc, f)
            f.flush()
            os.fsync(f.fileno())
        
        # Validate
        with open(temp_path, "r", encoding="utf-8") as f:
            tomlkit.load(f)
            
        os.replace(temp_path, CONFIG_PATH)
        return True, "Config successfully updated."
    except Exception as e:
        if temp_path.exists():
            temp_path.unlink()
        return False, f"Failed to save config: {e}"

def disable_proxy_config() -> Tuple[bool, str]:
    if not CONFIG_PATH.exists():
        return False, "Config file not found."
        
    backup_file(CONFIG_PATH)
    
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        try:
            doc = tomlkit.load(f)
        except Exception as e:
            return False, f"Failed to parse config.toml: {e}"
            
    state = _load_state()
    prev_provider = state.get("previous_model_provider", "openai")
    
    # Safe check for previous provider existence
    if prev_provider != "openai":
        providers = doc.get("model_providers", {})
        if prev_provider not in providers or prev_provider == "openai-idfix":
            prev_provider = "openai"
        
    doc["model_provider"] = prev_provider
    
    try:
        temp_path = CONFIG_PATH.with_suffix(f".tmp.{time.time()}")
        with open(temp_path, "w", encoding="utf-8") as f:
            tomlkit.dump(doc, f)
            f.flush()
            os.fsync(f.fileno())
            
        with open(temp_path, "r", encoding="utf-8") as f:
            tomlkit.load(f)
            
        os.replace(temp_path, CONFIG_PATH)
        return True, f"Restored default provider: {prev_provider}"
    except Exception as e:
        if temp_path.exists():
            temp_path.unlink()
        return False, f"Failed to restore config: {e}"
