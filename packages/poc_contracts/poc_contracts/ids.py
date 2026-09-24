"""Identifiers per Spec §5.2: poc_/run_/task_ + ULID; versions vNNN."""
import os
import re
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_PREFIXES = ("poc", "run", "task")
_ID_RE = re.compile(r"^(poc|run|task)_[0-9A-HJKMNP-TV-Z]{26}$")
_VER_RE = re.compile(r"^v(\d{3})$")


def _ulid() -> str:
    ts = int(time.time() * 1000)
    rnd = int.from_bytes(os.urandom(10), "big")
    n = (ts << 80) | rnd
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[n & 31])
        n >>= 5
    return "".join(reversed(out))


def new_id(prefix: str) -> str:
    """new_id('poc') -> 'poc_01J...' (time-ordered, 26-char Crockford ULID)."""
    if prefix not in _PREFIXES:
        raise ValueError(f"prefix must be one of {_PREFIXES}, got {prefix!r}")
    return f"{prefix}_{_ulid()}"


def is_id(value: str) -> bool:
    return bool(_ID_RE.match(value or ""))


def next_version(current: str | None) -> str:
    """next_version(None) -> 'v001'; next_version('v003') -> 'v004'."""
    if current is None:
        return "v001"
    m = _VER_RE.match(current)
    if not m:
        raise ValueError(f"bad version {current!r}, expected vNNN")
    return f"v{int(m.group(1)) + 1:03d}"
