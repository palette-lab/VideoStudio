import os
import sys
from pathlib import Path

os.environ["MOCK_STEP_SECONDS"] = "0"
os.environ.setdefault("ROUTER_ASK", "all")  # the existing suite covers every clarification path
_backend = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_backend / "api"), str(_backend)]  # router_core and eval

# The logic tests run against a fixed fixture registry (the original five illustrative apps), so they test the
# router itself. tests/test_real_registry.py checks routing against the real app profiles.
from router_core import config as _config  # noqa: E402

REAL_REGISTRY = _backend / "api" / "router_core" / "registry"
_config.REGISTRY_DIR = _backend / "tests" / "fixtures" / "registry_dummy"
