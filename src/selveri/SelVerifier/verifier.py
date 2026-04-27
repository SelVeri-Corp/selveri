from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional
from z3 import Not, Solver, simplify, sat, unsat, unknown
from sympy import Basic

from ..compiler import IRInstr
from ..defs import RuntimeConfiguration, FutureObligation
from ..errors import ParserError, VerificationError, VerifierRuntimeError
from ..spec_parser import parse_spec
from ..specs import ParsedSpec, RawSpec, Spec, SpecType, SpecFromBExp, SpecUnOp, SpecBinOp, SpecQuant
from .future_automaton import compile_future_automaton
from .future_mapper import FutureLTLMapper
from .mapper import Z3Mapper

class VerificationEngine:

    SOLVER_TIMEOUT = 60000 # 1 minute timeout for each solver check.

    def __init__(self):
        self.last_step = 0
        self.declaration_steps: Dict[str, int] = dict() # stores the declaration steps of variable
        self.initialization_steps : Dict[str, int] = dict() # stores the initial assignment steps of variables
        self.history: list[RuntimeConfiguration] = list()
        self.pltl_memo: Dict[int, Dict[Spec, bool]] = dict() # for pLTL verification memoization
        self.fltl_pending: list[FutureObligation] = list() # for future LTL verification pending obligations
        self.specs_by_id: Dict[int, ParsedSpec] = dict()
        self.prepared = False


    ################# Spec Compilation #################

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
        result = self.on_veri(parsed_spec, snapshot) # call the on_veri callback
        if parsed_spec.ast.type != SpecType.fLTL and not result:
            self.raise_spec_failure(
                parsed_spec.raw_spec,
                "the current execution state does not satisfy the specification",
            )
        return parsed_spec

    ################# ###########################    
    ################# Verifying #################
    #############################################        
    
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
    def on_veri(self, parsed_spec: ParsedSpec, snapshot: RuntimeConfiguration) -> bool:
        return self.verify(
            parsed_spec.ast,
            snapshot,
            raw_spec=parsed_spec.raw_spec,
        )

    def verify(
        self,
        spec: Spec,
        snapshot: RuntimeConfiguration,
        raw_spec: RawSpec,
        start_step : Optional[int] = None,
    ) -> bool:
        # lexical_depth captures the depth at which the spec is defined. This is passed to 
        # historical evaluations to ensure we don't resolve inner-scope variables that the spec shouldn't see.
        lexical_depth = snapshot.scope.depth
        if spec.type == SpecType.FOL:
            return self.verify_FOL(spec, snapshot, lexical_depth)
        elif spec.type == SpecType.pLTL:
            if start_step is None:
                start_step = self.pltl_deduce_inital_step(spec, snapshot.scope)
            return self.verify_past_LTL(spec, self.last_step - 1, start_step, lexical_depth)
        else: # spec.type == SpecType.fLTL:
            return self.verify_future_LTL(spec, snapshot, lexical_depth, raw_spec)
    
    ################# FOL Verification #################

    def verify_FOL(self, spec: Spec, snapshot: RuntimeConfiguration, lexical_depth: int) -> bool:
        solver = Solver()
        mapper = Z3Mapper(snapshot, solver, lexical_depth)
        negated_assumption = simplify(Not(mapper.map_FOL(spec)))
        solver.set("timeout", VerificationEngine.SOLVER_TIMEOUT); result = solver.check(negated_assumption)
        if result == unsat:
            return True
        elif result == sat:
            # TODO: consider giving a counter-example, using the model
            # model : ModelRef = solver.model()
            return False
        else: # unknown
            raise VerificationError(f"Z3-solver returned unknown for FOL verification. Reason: {solver.reason_unknown()}")


    ################# pLTL Verification #################

    def verify_past_LTL(self, spec: Spec, step : int, start_step : int, lexical_depth: int = 0) -> bool:
        if spec in self.pltl_memo[step]: # memoization for efficiency
            return self.pltl_memo[step][spec]
        
        if spec.type == SpecType.FOL:
            result = self.verify_FOL(spec, self.history[step], lexical_depth)
        
        elif isinstance(spec, SpecUnOp):
            if spec.op == "!":
                result = not self.verify_past_LTL(spec.rhs, step, start_step, lexical_depth)
            elif spec.op == "Previously":
                if step == start_step:
                    result = False
                else:
                    result = self.verify_past_LTL(spec.rhs, step - 1, start_step, lexical_depth)
            elif spec.op == "Once":
                if step == start_step:
                    result = self.verify_past_LTL(spec.rhs, step, start_step, lexical_depth)
                else:
                    result = self.verify_past_LTL(spec.rhs, step, start_step, lexical_depth) or self.verify_past_LTL(spec, step - 1, start_step, lexical_depth)
            elif spec.op == "Historically":
                if step == start_step:
                    result = self.verify_past_LTL(spec.rhs, step, start_step, lexical_depth)
                else:
                    result = self.verify_past_LTL(spec.rhs, step, start_step, lexical_depth) and self.verify_past_LTL(spec, step - 1, start_step, lexical_depth)
            else:
                raise VerificationError(f"Unsupported unary operator for pLTL: {spec.op}")

        elif isinstance(spec, SpecBinOp):
            if spec.op == "&&":
                result = self.verify_past_LTL(spec.left, step, start_step, lexical_depth) and self.verify_past_LTL(spec.right, step, start_step, lexical_depth)
            elif spec.op == "||":
                result = self.verify_past_LTL(spec.left, step, start_step, lexical_depth) or self.verify_past_LTL(spec.right, step, start_step, lexical_depth)
            elif spec.op == "=>":
                result = (not self.verify_past_LTL(spec.left, step, start_step, lexical_depth)) or self.verify_past_LTL(spec.right, step, start_step, lexical_depth)
            elif spec.op == "Since":
                right = self.verify_past_LTL(spec.right, step, start_step, lexical_depth)
                if step == start_step:
                    result = right
                else:
                    left = self.verify_past_LTL(spec.left, step, start_step, lexical_depth)
                    result = right or (left and self.verify_past_LTL(spec, step - 1, start_step, lexical_depth))
            else:
                raise VerificationError(f"Unsupported binary operator for pLTL: {spec.op}")
        
        else:
            raise VerificationError(f"Unexpected specification type for pLTL: {spec.type}")

        self.pltl_memo[step][spec] = result
        return result


    def pltl_deduce_inital_step(self, spec: Spec, lexical_scope: Any) -> int:
        variables = self._spec_get_free_variables(spec, set())
        inital_step = 0
        for var in variables:
            owner = lexical_scope.find_owner(var)
            if owner is None:
                raise VerificationError(f"Variable {var} not found in scope")
            var_z3_name = Z3Mapper.get_z3_var_name(var, owner.scope_id)
            if var_z3_name in self.initialization_steps:
                inital_step = max(inital_step, self.initialization_steps[var_z3_name])
            elif var_z3_name in self.declaration_steps:
                inital_step = max(inital_step, self.declaration_steps[var_z3_name])
            else:
                raise VerificationError(f"Variable {var} has no initialization or declaration step")
        return inital_step

    def register_declaration(self, name: str, scope_id: int) -> None:
        """Records the step when a variable is declared in a specific scope."""
        name_z3 = Z3Mapper.get_z3_var_name(name, scope_id)
        if name_z3 not in self.declaration_steps:
            self.declaration_steps[name_z3] = self.last_step

    def register_initial_assignment(self, name: str, scope: Any) -> None:
        """Records the step when a variable is first initialized/assigned, removing it from declarations."""
        owner = scope.find_owner(name)
        scope_id = owner.scope_id if owner else scope.scope_id
        name_z3 = Z3Mapper.get_z3_var_name(name, scope_id)
        if name_z3 not in self.initialization_steps:
            self.initialization_steps[name_z3] = self.last_step
            self.declaration_steps.pop(name_z3, None)

    def _spec_get_free_variables(self, spec: Spec, variables: set[str]) -> set[str]:
        if isinstance(spec, SpecFromBExp):
            self._bexp_get_free_variables(spec.bexp, variables)
        elif isinstance(spec, SpecUnOp):
            self._spec_get_free_variables(spec.rhs, variables)
        elif isinstance(spec, SpecBinOp):
            self._spec_get_free_variables(spec.left, variables)
            self._spec_get_free_variables(spec.right, variables)
        elif isinstance(spec, SpecQuant):
            self._domain_get_free_variables(spec.domain, variables)
            self._spec_get_free_variables(spec.body, variables)
            variables.discard(spec.var)  # bound variable is not free
        return variables

    def _bexp_get_free_variables(self, bexp: Any, variables: set[str]) -> None:
        from ..parser import BNot, BBinOp, BCompare, BTruthy, BBool
        if isinstance(bexp, BNot):
            self._bexp_get_free_variables(bexp.rhs, variables)
        elif isinstance(bexp, BBinOp):
            self._bexp_get_free_variables(bexp.left, variables)
            self._bexp_get_free_variables(bexp.right, variables)
        elif isinstance(bexp, BCompare):
            self._aexp_get_free_variables(bexp.left, variables)
            self._aexp_get_free_variables(bexp.right, variables)
        elif isinstance(bexp, BTruthy):
            self._aexp_get_free_variables(bexp.aexp, variables)

    def _aexp_get_free_variables(self, aexp: Any, variables: set[str]) -> None:
        from ..parser import AVar, ALen, AIndex, AUnOp, ABinOp, IntLit, FloatLit, FuncCall
        from ..spec_parser import ABoundVar
        if isinstance(aexp, ABoundVar):
            pass  # bound variables are not free; handled by _spec_get_free_variables via SpecQuant.discard
        elif isinstance(aexp, AVar):
            variables.add(aexp.name)
        elif isinstance(aexp, ALen):
            variables.add(aexp.name)
        elif isinstance(aexp, AIndex):
            self._aexp_get_free_variables(aexp.base, variables)
            self._aexp_get_free_variables(aexp.index, variables)
        elif isinstance(aexp, AUnOp):
            self._aexp_get_free_variables(aexp.rhs, variables)
        elif isinstance(aexp, ABinOp):
            self._aexp_get_free_variables(aexp.left, variables)
            self._aexp_get_free_variables(aexp.right, variables)
        elif isinstance(aexp, FuncCall):
            for arg in aexp.args:
                self._aexp_get_free_variables(arg, variables)
        # IntLit, FloatLit, ListLit — no variables

    def _domain_get_free_variables(self, domain: Any, variables: set[str]) -> None:
        from ..specs import DomainIdent, DomainValues, DomainRange, DomainInterval
        if domain is None:
            return
        elif isinstance(domain, DomainIdent):
            variables.add(domain.name)
        elif isinstance(domain, DomainValues):
            for item in domain.items:
                self._aexp_get_free_variables(item, variables)
        elif isinstance(domain, DomainRange):
            self._aexp_get_free_variables(domain.lo, variables)
            self._aexp_get_free_variables(domain.hi, variables)
        elif isinstance(domain, DomainInterval):
            self._aexp_get_free_variables(domain.lo, variables)
            self._aexp_get_free_variables(domain.hi, variables)
        # DomainType, DomainVar — no runtime variables


    ################# fLTL Verification #################

    def verify_future_LTL(
        self,
        spec: Spec,
        snapshot: RuntimeConfiguration,
        lexical_depth: int,
        raw_spec: RawSpec,
    ) -> bool:
        '''
        Verify a future LTL specification.
        '''
        mapped_formula = FutureLTLMapper().map(spec)
        automaton = compile_future_automaton(
            mapped_formula.formula_text,
            mapped_formula.atom_table.keys(),
        )
        obligation = FutureObligation(
            spec_id=raw_spec.spec_id,
            source_spec=raw_spec.text,
            created_at_step=self.last_step,
            scope_id=snapshot.scope.scope_id,
            lexical_depth=lexical_depth,
            automaton=automaton,
            atom_table=mapped_formula.atom_table,
            current_state=automaton.initial_state,
            steps_to_skip=0,
        )
        self.advance_future_obligation(obligation, snapshot) # TODO: is it correct to directly move from the initial state?
        self.fltl_pending.append(obligation)
        return True

    def evaluate_future_atoms(
        self,
        snapshot: RuntimeConfiguration,
        atom_table: Dict[str, Spec],
        lexical_depth: int,
    ) -> Dict[str, bool]:
        '''
        Evaluate the future atoms based on the snapshot.
        '''
        return {
            atom_name: self.verify_FOL(atom_spec, snapshot, lexical_depth) # for each atom, verify the FOL specification
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
        atom_values = self.evaluate_future_atoms(
            snapshot,
            obligation.atom_table,
            obligation.lexical_depth,
        )
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

    def on_scope_exit(self, leaving_scope_id: int) -> None:
        '''
        Handle the exit of a scope.
        '''
        to_remove: list[int] = []
        for idx, obligation in enumerate(self.fltl_pending):
            if obligation.scope_id == leaving_scope_id:
                to_remove.append(idx)
        for idx in reversed(to_remove):
            self.fltl_pending.pop(idx)

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
