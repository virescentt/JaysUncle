import pytest


def call_or_skip(fn, *args):
    """Call fn; if it is still a stub (NotImplementedError) → skip the test instead of failing.
    Specification tests turn green automatically once the function is implemented."""
    try:
        return fn(*args)
    except NotImplementedError:
        pytest.skip(f"{fn.__name__}: not implemented yet")
