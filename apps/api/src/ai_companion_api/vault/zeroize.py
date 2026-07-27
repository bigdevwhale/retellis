"""In-memory zeroization for decrypted key material.

Decrypted BYOK keys live only for one LiteLLM call. This context manager
ensures the underlying bytearray is overwritten with zeros on exit — whether
the call succeeded or raised — so keys never leak into heap snapshots or
swapper diagnostics. Plaintext keys are never persisted, never logged, and
never returned by any endpoint.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def zeroized(buf: bytearray) -> Iterator[bytearray]:
    """Yield ``buf``; overwrite it with zeros on exit, even on exception."""
    try:
        yield buf
    finally:
        for i in range(len(buf)):
            buf[i] = 0
