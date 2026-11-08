"""alias of clean runtime via stage_clean_runtime.py"""
import importlib.util
from pathlib import Path
_src = Path(__file__).resolve().with_name("stage_clean_runtime.py")
_spec = importlib.util.spec_from_file_location("stage_clean_runtime_shared", str(_src))
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
for _name in dir(_mod):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_mod, _name)
