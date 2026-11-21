"""Validator registry. YAML fields reference validators by name: `validate: [nip]`.

Validators are SOFT: they never delete a value, they only adjust its confidence
(synthetic training data often has invalid checksums — NIP 115/189 valid, IBAN 43/120 valid).
Contract:  f(value) -> bool.  NotImplementedError → validator ignored.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

VALIDATORS: dict[str, Callable[[Any], bool]] = {}


def validator(name: str):
    def deco(fn: Callable[[Any], bool]) -> Callable[[Any], bool]:
        VALIDATORS[name] = fn
        return fn

    return deco


def run_validators(names: list[str], value: Any) -> dict[str, bool | None]:
    """{validator_name: True/False/None(not implemented or error)}"""
    out: dict[str, bool | None] = {}
    for name in names:
        fn = VALIDATORS.get(name)
        if fn is None or value is None:
            out[name] = None
            continue
        try:
            out[name] = bool(fn(value))
        except NotImplementedError:
            out[name] = None
        except Exception:  # noqa: BLE001
            out[name] = False
    return out


from odwolania2json.validate import checksums as _checksums  # noqa: E402,F401
