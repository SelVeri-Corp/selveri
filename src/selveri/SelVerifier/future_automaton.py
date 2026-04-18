from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

from sympy import Symbol, false, sympify, true

from ltlf2dfa.base import MonaProgram
from ltlf2dfa.ltlf2dfa import output2dot
from ltlf2dfa.parser.ltlf import LTLfParser

from ..defs import FutureAutomaton, FutureTransition
from ..errors import VerificationError

INITIAL_STATE_RE = re.compile(r"init\s*->\s*([A-Za-z0-9_]+)\s*;")
ACCEPTING_STATES_RE = re.compile(r"node\s*\[shape\s*=\s*doublecircle\];\s*(.*?)\s*;")
TRANSITION_RE = re.compile(
    r"([A-Za-z0-9_]+)\s*->\s*([A-Za-z0-9_]+)\s*\[label=\"(.*)\"\]\s*;"
)
STATE_NAME_RE = re.compile(r"[A-Za-z0-9_]+")

def compile_future_automaton(formula_text: str, atom_names: Iterable[str]) -> FutureAutomaton:
    try:
        ltlf_formula = LTLfParser()(formula_text).to_nnf() # negation normal form
    except Exception as exc:
        raise VerificationError(
            f"Failed to parse future LTL formula '{formula_text}': {exc}"
        ) from None

    # manually construct the to_dfa() pipeline to get better control
    mona_program = MonaProgram(ltlf_formula).mona_program()
    mona_output = invoke_mona_program(mona_program)
    dot_output = output2dot(mona_output)

    # simplest way to get the automaton is to parse the dot output
    return parse_dot_automaton(formula_text, dot_output, atom_names)

def invoke_mona_program(mona_program: str) -> str:
    '''
    Invoke the MONA program to generate the automaton.
    '''
    mona_path = shutil.which("mona") # try to find mona
    if mona_path is None:
        raise VerificationError(
            "Future LTL verification requires the 'mona' executable to be available on PATH."
        )

    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".mona",
        delete=False,
    )
    mona_file = Path(handle.name)
    try:
        with handle:
            handle.write(mona_program)

        result = subprocess.run(
            [mona_path, "-q", "-u", "-w", str(mona_file)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VerificationError("MONA timed out while building the future-LTL automaton.") from exc
    finally:
        mona_file.unlink(missing_ok=True)

    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise VerificationError(f"MONA failed while building the future-LTL automaton: {details}")

    return result.stdout.strip()

def parse_dot_automaton(
    formula_text: str,
    dot_output: str,
    atom_names: Iterable[str],
) -> FutureAutomaton:
    initial_state: str | None = None
    accepting_states: set[str] = set()
    all_states: set[str] = set()
    transitions_by_state: dict[str, list[FutureTransition]] = {}
    guard_locals = build_guard_locals(atom_names)

    for raw_line in dot_output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        initial_match = INITIAL_STATE_RE.fullmatch(line)
        if initial_match is not None:
            initial_state = initial_match.group(1)
            all_states.add(initial_state)
            continue

        accepting_match = ACCEPTING_STATES_RE.fullmatch(line)
        if accepting_match is not None:
            accepting_states.update(STATE_NAME_RE.findall(accepting_match.group(1)))
            all_states.update(accepting_states)
            continue

        transition_match = TRANSITION_RE.fullmatch(line)
        if transition_match is None:
            continue

        source_state, target_state, guard_text = transition_match.groups()
        guard_formula = sympify(guard_text, locals=guard_locals)
        transition = FutureTransition(
            source_state=source_state,
            target_state=target_state,
            guard_text=guard_text,
            guard_formula=guard_formula,
        )
        transitions_by_state.setdefault(source_state, []).append(transition)
        all_states.add(source_state)
        all_states.add(target_state)

    if initial_state is None:
        raise VerificationError("Generated future-LTL automaton is missing an initial state.")

    for state in all_states:
        transitions_by_state.setdefault(state, [])

    # directly prune states that cannot reach an accepting state
    can_reach_accepting = compute_can_reach_accepting(all_states, accepting_states, transitions_by_state)
    
    # freeze the transitions to make them immutable
    frozen_transitions = {
        state: tuple(transitions)
        for state, transitions in transitions_by_state.items()
    }
    return FutureAutomaton(
        formula_text=formula_text,
        initial_state=initial_state,
        accepting_states=frozenset(accepting_states),
        transitions_by_state=frozen_transitions,
        can_reach_accepting=frozenset(can_reach_accepting),
    )

def build_guard_locals(atom_names: Iterable[str]) -> dict[str, object]:
    guard_locals: dict[str, object] = {
        "true": true,
        "false": false,
    }
    for atom_name in atom_names:
        guard_locals[atom_name] = Symbol(atom_name)
    return guard_locals

def compute_can_reach_accepting(
    all_states: set[str],
    accepting_states: set[str],
    transitions_by_state: dict[str, list[FutureTransition]],
) -> set[str]:
    reverse_edges: dict[str, set[str]] = {state: set() for state in all_states}
    for source_state, transitions in transitions_by_state.items():
        for transition in transitions:
            reverse_edges.setdefault(transition.target_state, set()).add(source_state)

    worklist = list(accepting_states)
    can_reach_accepting = set(accepting_states)
    while worklist:
        state = worklist.pop()
        for predecessor in reverse_edges.get(state, set()):
            if predecessor in can_reach_accepting:
                continue
            can_reach_accepting.add(predecessor)
            worklist.append(predecessor)
    return can_reach_accepting
