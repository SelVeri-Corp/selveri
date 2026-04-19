from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from sympy import Basic
from z3 import Not, Solver, simplify, unsat

from ..compiler import IRInstr
from ..defs import RuntimeConfiguration, FutureObligation
from ..errors import ParserError, VerificationError, VerifierRuntimeError
from ..spec_parser import parse_spec
from ..specs import ParsedSpec, RawSpec, Spec, SpecType, SpecUnOp, SpecBinOp, SpecQuant
from .future_automaton import compile_future_automaton
from .future_mapper import FutureLTLMapper
from .mapper import Z3Mapper

class VerificationEngine:
    def __init__(self):
        self.last_step = 0
        self.history: list[RuntimeConfiguration] = []
        self.pltl_memo: Dict[int, Dict[Spec, bool]] = dict() # for pLTL verification memoization
        self.fltl_pending: list[FutureObligation] = [] # for future LTL verification pending obligations
        self.specs_by_id: Dict[int, ParsedSpec] = {}
        self.prepared = False

    # the verifier owns spec parsing and caches parsed ASTs before execution starts.
    def prepare_program(
        self,
        program: Iterable[IRInstr],
        *,
        raw_specs: Optional[Iterable[RawSpec]] = None,
    ) -> None:
        collected_specs = list(raw_specs) if raw_specs is not None else self.collect_raw_specs(program) # collect raw specs from the program
        
        self.specs_by_id.clear()
        for raw_spec in collected_specs: # parse and register the raw specs
            parsed_spec = self.parse_raw_spec(raw_spec)
            spec_id = parsed_spec.raw_spec.spec_id
            if spec_id in self.specs_by_id:
                raise VerificationError(f"Duplicate specification id: {spec_id}")
            self.specs_by_id[spec_id] = parsed_spec

        self.prepared = True # the program is prepared

    def collect_raw_specs(self, program: Iterable[IRInstr]) -> List[RawSpec]:
        raw_specs: List[RawSpec] = []
        for instr in program:
            if instr.op != "VERI":
                continue
            if len(instr.args) < 2:
                raise VerificationError("Malformed VERI instruction encountered during verifier preparation.")
            raw_specs.append(
                RawSpec(
                    spec_id=int(instr.args[0]),
                    text=str(instr.args[1]),
                )
            )
        return raw_specs

    def parse_raw_spec(self, raw_spec: RawSpec) -> ParsedSpec:
        try:
            spec_ast = parse_spec(raw_spec.text)
        except ParserError as exc:
            if raw_spec.location is None:
                raise VerificationError(
                    f"Failed to parse specification #{raw_spec.spec_id}: {exc}"
                ) from None
            raise VerifierRuntimeError(
                "Failed to parse specification "
                f"#{raw_spec.spec_id} at "
                f"{raw_spec.location.start.line}:{raw_spec.location.start.column}: {exc}"
            ) from None
        return ParsedSpec(raw_spec=raw_spec, ast=spec_ast)

    def resolve_spec(self, spec_id: int) -> ParsedSpec: # resolve a spec by its id
        if not self.prepared:
            raise VerificationError("Specifications must be prepared before execution starts.")
        if spec_id not in self.specs_by_id:
            raise VerificationError(f"Unknown specification id: {spec_id}")
        return self.specs_by_id[spec_id]

    def handle_veri(self, spec_id: int, snapshot: RuntimeConfiguration) -> ParsedSpec:
        parsed_spec = self.resolve_spec(spec_id) # resolve the spec by its id
        result = self.on_veri(parsed_spec.ast, snapshot) # call the on_veri callback
        if parsed_spec.ast.type != SpecType.fLTL and not result:
            self.raise_spec_failure(
                parsed_spec.raw_spec,
                "the current execution state does not satisfy the specification",
            )
        return parsed_spec

    def on_program_start(self) -> None:
        self.last_step = 0
        self.history.clear()
        self.pltl_memo.clear()
        self.fltl_pending.clear()

    def on_step(self, snapshot: RuntimeConfiguration) -> None:
        # TODO: investigate the memory management here
        self.history.append(snapshot)
        self.pltl_memo[self.last_step] = dict()
        self.last_step += 1
        self.advance_future_obligations(snapshot)

    # TODO: consider optimizations: updating the mapper at each IR assignment and declaration, then use push/pop instead of reset
    def on_veri(self, spec: Spec, snapshot: RuntimeConfiguration) -> bool:
        return self.verify(spec, snapshot)

    def verify(
        self,
        spec: Spec,
        snapshot: RuntimeConfiguration,
        raw_spec: Optional[RawSpec] = None,
    ) -> bool:
        if spec.type == SpecType.FOL:
            return self.verify_FOL(spec, snapshot)
        elif spec.type == SpecType.pLTL:
            return self.verify_past_LTL(spec, self.last_step - 1) # decrement one as it was just incremented
        else: # spec.type == SpecType.fLTL:
            return self.verify_future_LTL(spec, snapshot, raw_spec)
    
    def verify_FOL(self, spec: Spec, snapshot: RuntimeConfiguration) -> bool:
        solver = Solver()
        mapper = Z3Mapper(snapshot, solver)
        negated_assumption = simplify(Not(mapper.map_FOL(spec)))
        result = solver.check(negated_assumption)
        if result == unsat:
            return True
        else: # sat
            # TODO: consider giving a counter-example, using the model
            # model : ModelRef = solver.model()
            return False

    # TODO: check the base cases
    def verify_past_LTL(self, spec: Spec, step : int, start_step : int = 0) -> bool:
        if spec in self.pltl_memo[step]: # memoization for efficiency
            return self.pltl_memo[step][spec]

        if spec.type == SpecType.FOL:
            result = self.verify_FOL(spec, self.history[step])
        
        elif isinstance(spec, SpecUnOp):
            if spec.op == "!":
                result = not self.verify_past_LTL(spec.rhs, step, start_step)
            elif spec.op == "Previously":
                if step == start_step:
                    result = False
                else:
                    result = self.verify_past_LTL(spec.rhs, step - 1, start_step)
            elif spec.op == "Once":
                if step == start_step:
                    result = self.verify_past_LTL(spec.rhs, step, start_step)
                else:
                    result = self.verify_past_LTL(spec.rhs, step, start_step) or self.verify_past_LTL(spec, step - 1, start_step)
            elif spec.op == "Historically":
                if step == start_step:
                    result = self.verify_past_LTL(spec.rhs, step, start_step)
                else:
                    result = self.verify_past_LTL(spec.rhs, step, start_step) and self.verify_past_LTL(spec, step - 1, start_step)

        elif isinstance(spec, SpecBinOp):
            if spec.op == "&&":
                result = self.verify_past_LTL(spec.left, step, start_step) and self.verify_past_LTL(spec.right, step, start_step)
            elif spec.op == "||":
                result = self.verify_past_LTL(spec.left, step, start_step) or self.verify_past_LTL(spec.right, step, start_step)
            elif spec.op == "=>":
                result = (not self.verify_past_LTL(spec.left, step, start_step)) or self.verify_past_LTL(spec.right, step, start_step)
            elif spec.op == "Since":
                right = self.verify_past_LTL(spec.right, step, start_step)
                if step == start_step:
                    result = right
                else:
                    left = self.verify_past_LTL(spec.left, step, start_step)
                    result = right or (left and self.verify_past_LTL(spec, step - 1, start_step))
        
        else:
            raise VerificationError(f"Unexpected specification type for pLTL: {spec.type}")

        self.pltl_memo[step][spec] = result
        return result

    def verify_future_LTL(
        self,
        spec: Spec,
        snapshot: RuntimeConfiguration,
        raw_spec: Optional[RawSpec],
    ) -> bool:
        if raw_spec is None:
            raise VerificationError("Future LTL verification requires a raw specification payload.")

        mapped_formula = FutureLTLMapper().map(spec)
        automaton = compile_future_automaton(
            mapped_formula.formula_text,
            mapped_formula.atom_table.keys(),
        )
        obligation = FutureObligation(
            spec_id=raw_spec.spec_id,
            source_spec=raw_spec.text,
            created_at_step=self.last_step,
            automaton=automaton,
            atom_table=mapped_formula.atom_table,
            current_state=automaton.initial_state,
            steps_to_skip=0,
        )
        self.advance_future_obligation(obligation, snapshot)
        self.fltl_pending.append(obligation)
        return True

    def evaluate_future_atoms(
        self,
        snapshot: RuntimeConfiguration,
        atom_table: Dict[str, Spec],
    ) -> Dict[str, bool]:
        '''
        Evaluate the future atoms based on the snapshot.
        '''
        return {
            atom_name: self.verify_FOL(atom_spec, snapshot) # for each atom, verify the FOL specification
            for atom_name, atom_spec in atom_table.items()
        }

    def advance_future_obligations(self, snapshot: RuntimeConfiguration) -> None:
        '''
        Advance all the future obligations by one step.
        '''
        for obligation in self.fltl_pending:
            if obligation.steps_to_skip > 0:
                obligation.steps_to_skip -= 1
                continue
            self.advance_future_obligation(obligation, snapshot)

    def advance_future_obligation(
        self,
        obligation: FutureObligation,
        snapshot: RuntimeConfiguration,
    ) -> None:
        '''
        Advance the future obligation by one step.
        '''
        atom_values = self.evaluate_future_atoms(snapshot, obligation.atom_table)
        transition = self.select_future_transition(obligation, atom_values)
        obligation.current_state = transition.target_state

        if obligation.current_state not in obligation.automaton.can_reach_accepting:
            self.raise_future_failure(
                obligation,
                f"the automaton reached state {obligation.current_state}, which cannot reach an accepting state",
            )

    def select_future_transition(
        self,
        obligation: FutureObligation,
        atom_values: Dict[str, bool],
    ):
        '''
        Select the transition to be taken by the future obligation based on the atom values.
        '''
        transitions = obligation.automaton.transitions_by_state.get(obligation.current_state, ())
        matching_transitions = [
            transition
            for transition in transitions
            if self.evaluate_guard(transition.guard_formula, atom_values)
        ]

        if len(matching_transitions) == 1:
            return matching_transitions[0]
        if len(matching_transitions) == 0:
            raise VerificationError(
                f"Future specification #{obligation.spec_id} has no matching transition "
                f"from automaton state {obligation.current_state}."
            )
        raise VerificationError(
            f"Future specification #{obligation.spec_id} has multiple matching transitions "
            f"from automaton state {obligation.current_state}."
        )

    def evaluate_guard(self, guard_formula: Basic, atom_values: Dict[str, bool]) -> bool:
        '''
        Evaluate the guard formula based on the atom values.
        '''
        substituted_guard = guard_formula.subs( # substitute the atom values into the guard formula
            {
                symbol: atom_values.get(str(symbol), False)
                for symbol in getattr(guard_formula, "free_symbols", set())
            }
        )
        return bool(substituted_guard) # return the boolean value of the substituted guard formula

    def on_program_end(self, final_snapshot: RuntimeConfiguration) -> None:
        '''
        Check if the state reached by the future obligations is an accepting state.
        '''
        self.advance_future_obligations(final_snapshot)
        for obligation in self.fltl_pending:
            if obligation.current_state not in obligation.automaton.accepting_states:
                self.raise_future_failure(
                    obligation,
                    f"the execution ended in non-accepting automaton state {obligation.current_state}",
                )

    def raise_spec_failure(self, raw_spec: RawSpec, detail: str) -> None:
        raise VerificationError(
            f"Specification #{raw_spec.spec_id} failed: {detail}: {raw_spec.text}"
        )

    def raise_future_failure(self, obligation: FutureObligation, detail: str) -> None:
        raise VerificationError(
            f"Future specification #{obligation.spec_id} failed: {detail}: {obligation.source_spec}"
        )
