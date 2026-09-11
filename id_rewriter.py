"""
id_rewriter.py — The single source of truth for Codex synthetic ID rewriting.
"""

import re
from typing import Any, Tuple, Dict, Set, List

import copy

MSG_ID_RE = re.compile(
    r"^resp_([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})_msg$"
)
ITEM_HEX_RE = re.compile(r"^item_([0-9a-fA-F]{16,})$", re.IGNORECASE)

OFFICIAL_PREFIXES = {
    "message": "msg_",
    "reasoning": "rs_",
    "function_call": "fc_",
    "function_call_output": "fco_",
    "computer_call": "cc_",
    "computer_call_output": "cco_",
}

ID_KEYS = frozenset({
    "id", "item_id", "message_id", "previous_item_id", "parent_id", "response_id"
})


def rewrite_single_id(raw_id: str, item_type: str, id_map: dict) -> str:
    """Rewrite a single ID based on its known type (if available)."""
    if not isinstance(raw_id, str) or not raw_id:
        return raw_id
    if raw_id in id_map:
        return id_map[raw_id]

    # Pattern 1
    m1 = MSG_ID_RE.match(raw_id)
    if m1:
        new_id = f"msg_{m1.group(1)}"
        id_map[raw_id] = new_id
        return new_id

    # Pattern 2
    m2 = ITEM_HEX_RE.match(raw_id)
    if m2:
        if item_type == "reasoning":
            return raw_id  # Caller should handle dropping for safe_reasoning
        
        prefix = OFFICIAL_PREFIXES.get(item_type)
        if prefix:
            new_id = prefix + m2.group(1)
            id_map[raw_id] = new_id
            return new_id

    return raw_id


def _walk_and_rewrite(obj: Any, id_map: dict, dropped_ids: set | None = None) -> int:
    """Recursively walks JSON structures to rewrite IDs and clear dangling references."""
    fixes = 0
    if dropped_ids is None:
        dropped_ids = set()

    if isinstance(obj, dict):
        itype = obj.get("type", "")
        # Remove dangling references
        for ref_key in (ID_KEYS - {"id"}):
            val = obj.get(ref_key)
            if val in dropped_ids:
                obj.pop(ref_key, None)
                fixes += 1

        for k in list(obj.keys()):
            v = obj[k]
            if k in ID_KEYS and isinstance(v, str) and v:
                new_v = rewrite_single_id(v, itype if k == "id" else "", id_map)
                if new_v != v:
                    obj[k] = new_v
                    fixes += 1
            elif k not in ("content", "text") and isinstance(v, (dict, list)):
                fixes += _walk_and_rewrite(v, id_map, dropped_ids)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                fixes += _walk_and_rewrite(item, id_map, dropped_ids)
    return fixes


def _extract_items(obj: Any, out_items: List[Dict]) -> None:
    """Find dicts that look like codex items (having 'id' and 'type')."""
    if isinstance(obj, dict):
        if "id" in obj and "type" in obj and isinstance(obj["id"], str) and isinstance(obj["type"], str):
            out_items.append(obj)
        for k, v in obj.items():
            if k not in ("content", "text"):
                _extract_items(v, out_items)
    elif isinstance(obj, list):
        for item in obj:
            _extract_items(item, out_items)


def sanitise_input_array(
    input_arr: List[Any], 
    id_map: Dict[str, str],
    dropped_ids: Set[str] | None = None,
    safe_reasoning: bool = True
) -> Tuple[List[Any], Dict[str, str], int, int]:
    """
    Sanitise an array of items or envelopes.
    Returns (new_array, updated_id_map, fixes_count, dropped_count)
    """
    if dropped_ids is None:
        dropped_ids = set()
        
    new_arr = []
    fixes = 0
    drops = 0

    for item in input_arr:
        if not isinstance(item, dict):
            new_arr.append(item)
            continue
            
        item_copy = copy.deepcopy(item)
        
        # Determine if this item or any nested payload contains a bad reasoning item
        all_inner_items: List[Dict] = []
        _extract_items(item_copy, all_inner_items)
        
        should_drop_entire_element = False
        for inner in all_inner_items:
            i_type = inner.get("type", "")
            raw_id = inner.get("id", "")
            if i_type == "reasoning" and safe_reasoning:
                if not raw_id.startswith("rs_"):
                    dropped_ids.add(raw_id)
                    should_drop_entire_element = True
                    drops += 1
                    
        if should_drop_entire_element:
            # We drop the top-level element if any of its internal items is a bad reasoning block
            continue
            
        # Recursive rewrite
        item_fixes = _walk_and_rewrite(item_copy, id_map, dropped_ids)
        fixes += item_fixes
        new_arr.append(item_copy)

    return new_arr, id_map, fixes, drops


def rewrite_response_object(
    obj: Dict[str, Any], id_map: Dict[str, str] | None = None
) -> Tuple[Dict[str, Any], Dict[str, str], int]:
    """
    Rewrite IDs in an incoming response object from upstream.
    Does not drop reasoning items since they come from the real server.
    """
    if id_map is None:
        id_map = {}
        
    obj_copy = copy.deepcopy(obj)
    fixes = _walk_and_rewrite(obj_copy, id_map)
    return obj_copy, id_map, fixes
