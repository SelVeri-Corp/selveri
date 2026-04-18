from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from sympy.core.basic import Basic
from .runtime import DeclType
from .specs import Spec

class AExp: pass
class BExp: pass
class Stmt: pass

@dataclass(frozen=True)
class RuntimeConfiguration:
    state: Dict[str, Any]
    scope: Dict[str, Optional[DeclType]]
    
@dataclass(frozen=True)
class Formula:
    uid: int

@dataclass(frozen=True)
class FutureTransition:
    source_state: str
    target_state: str
    guard_text: str
    guard_formula: Basic

@dataclass(frozen=True)
class FutureAutomaton:
    formula_text: str
    initial_state: str
    accepting_states: frozenset[str]
    transitions_by_state: Dict[str, tuple[FutureTransition, ...]]
    can_reach_accepting: frozenset[str]

@dataclass
class FutureObligation:
    spec_id: int
    source_spec: str
    created_at_step: int
    automaton: FutureAutomaton
    atom_table: Dict[str, Spec]
    current_state: str
    steps_to_skip: int # added for flexibility to skip some amount of steps for a certain spec
