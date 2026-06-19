"""Typed facade over the in-process `liwe` PyO3 binding (`agent_memory._iwe`).

The operations layer calls these functions instead of shelling out to the `iwe` binary.
Each call loads the vault graph in-process, runs liwe's own operation, persists the
resulting changes, and returns the value the operations layer expects. Failures (missing
key, ambiguous section/reference) raise loudly from the binding.
"""

from __future__ import annotations

from pathlib import Path

from agent_memory import _iwe


def retrieve(vault: Path, key: str) -> str:
    return _iwe.retrieve(str(vault), key)


def squash(vault: Path, key: str, depth: int) -> str:
    return _iwe.squash(str(vault), key, depth)


def rename(vault: Path, old_key: str, new_key: str) -> None:
    _iwe.rename(str(vault), old_key, new_key)


def delete(vault: Path, key: str) -> None:
    _iwe.delete(str(vault), key)


def extract(vault: Path, key: str, section: str) -> list[str]:
    return _iwe.extract(str(vault), key, section)


def inline(vault: Path, key: str, reference: str) -> list[str]:
    return _iwe.inline(str(vault), key, reference)
