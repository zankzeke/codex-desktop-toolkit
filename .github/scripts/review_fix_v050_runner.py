from pathlib import Path
import runpy

script = Path(__file__).with_name("review_fix_v050.py")
text = script.read_text(encoding="utf-8")
old = '''def replace_once(text: str, old: str, new: str, label: str) -> str:\n    count = text.count(old)\n    if count != 1:\n        raise RuntimeError(f"{label}: expected exactly one match, found {count}")\n    return text.replace(old, new, 1)\n'''
new = '''def replace_once(text: str, old: str, new: str, label: str) -> str:\n    count = text.count(old)\n    if count == 1:\n        return text.replace(old, new, 1)\n    # The same two-line keyword tail occurs once in make_app and once in main.\n    # The make_app replacement intentionally consumes the first occurrence;\n    # the later main replacement consumes the remaining one with more context.\n    if label == "make_app constructor args" and count == 2:\n        return text.replace(old, new, 1)\n    raise RuntimeError(f"{label}: expected exactly one match, found {count}")\n'''
if old not in text:
    raise SystemExit("review fixer helper signature changed unexpectedly")
script.write_text(text.replace(old, new, 1), encoding="utf-8")
runpy.run_path(str(script), run_name="__main__")
