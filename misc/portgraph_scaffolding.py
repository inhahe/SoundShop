from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Optional, Set


class PortGraph:
    """
    Minimal concrete port container.

    This is NOT your DSP graph. It's just a declaration/registry of which
    port names exist on a track, so connect()/validate_ports() can work.

    Later, your real DSP graph can either:
      - subclass PortGraph, or
      - wrap it, or
      - implement the same 4 methods and be used directly.
    """
    def input_ports(self) -> Set[str]:
        raise NotImplementedError

    def output_ports(self) -> Set[str]:
        raise NotImplementedError

    def ensure_input_port(self, name: str) -> None:
        raise NotImplementedError

    def ensure_output_port(self, name: str) -> None:
        raise NotImplementedError


@dataclass
class SimplePortGraph(PortGraph):
    """
    A simple concrete PortGraph that just stores the port names.

    Optional mapping fields are there in case you want to associate a port
    with an internal node id later (without changing the API).
    """
    _ins: Set[str] = field(default_factory=set)
    _outs: Set[str] = field(default_factory=set)

    # Optional: attach metadata (e.g., internal node/port ids) later
    in_meta: Dict[str, object] = field(default_factory=dict)
    out_meta: Dict[str, object] = field(default_factory=dict)

    def input_ports(self) -> Set[str]:
        return set(self._ins)

    def output_ports(self) -> Set[str]:
        return set(self._outs)

    def ensure_input_port(self, name: str) -> None:
        self._ins.add(str(name))

    def ensure_output_port(self, name: str) -> None:
        self._outs.add(str(name))