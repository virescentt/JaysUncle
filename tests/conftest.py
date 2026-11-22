import os

import pytest

# Tests never call a real LLM (no network, no costs, deterministic): an empty LLM_BASE_URL wins over .env
# (python-dotenv does not override variables that are already set). Set ODWOLANIA2JSON_TEST_LLM=1 to allow it.
if os.environ.get("ODWOLANIA2JSON_TEST_LLM") != "1":
    os.environ["LLM_BASE_URL"] = ""


def call_or_skip(fn, *args):
    """Call fn; if it is still a stub (NotImplementedError) → skip the test instead of failing.
    Specification tests turn green automatically once the function is implemented."""
    try:
        return fn(*args)
    except NotImplementedError:
        pytest.skip(f"{fn.__name__}: not implemented yet")
