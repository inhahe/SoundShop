from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple

from .core import Signal, _node_key, _rebuild_node

@dataclass
class CanonResult:
    root: Signal
    replaced: int

def canonicalize(root: Signal) -> CanonResult:
    """
    Returns a structurally deduped version of the signal DAG.
    Only dedupes node types with stable structural keys.
    """
    memo: Dict[int, Signal] = {}
    intern: Dict[Tuple[Any, ...], Signal] = {}
    replaced = 0

    def rec(n: Signal) -> Signal:
        nonlocal replaced
        if n.id in memo:
            return memo[n.id]

        ch = tuple(rec(c) for c in n.children())
        child_ids = tuple(c.id for c in ch)
        key = _node_key(n, child_ids)

        existing = intern.get(key)
        if existing is not None:
            memo[n.id] = existing
            replaced += 1
            return existing

        new_node = _rebuild_node(n, ch)

        intern[key] = new_node
        memo[n.id] = new_node
        return new_node

    return CanonResult(root=rec(root), replaced=replaced)
