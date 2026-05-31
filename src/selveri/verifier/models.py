from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict

from sympy.core.basic import Basic

from selveri.common.diagnostics import SourceSpan

if TYPE_CHECKING:
    from selveri.spec.models import Spec


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
    source_span: SourceSpan | None
    created_at_step: int
    scope_id: int
    lexical_depth: int
    automaton: FutureAutomaton
    atom_table: Dict[str, Spec]
    current_state: str
    steps_to_skip: int
