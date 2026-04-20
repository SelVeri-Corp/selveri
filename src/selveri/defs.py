from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .runtime import Scope, State


class AExp: pass
class BExp: pass
class Stmt: pass

@dataclass(frozen=True)
class RuntimeConfiguration:
    state: State
    scope: Scope
    
@dataclass(frozen=True)
class Formula:
    uid: int

@dataclass
class TemporalObligation:
    kind: str
    formula: Any
    aux_formula: Any | None
    created_at_step: int
    source_spec: str