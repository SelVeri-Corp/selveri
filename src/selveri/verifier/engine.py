from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple
from z3 import Not, Solver, simplify, sat, unsat, unknown
from sympy import Basic

from selveri.abstract_machine.config import RuntimeConfiguration
from selveri.common.diagnostics import DiagnosticCode, DiagnosticLabel
from selveri.common.types import real
from selveri.common.errors import ParserError, SpecDomainBoundsError, VerificationError, VerifierRuntimeError
from selveri.spec.models import ParsedSpec, RawSpec, Spec, SpecBinOp, SpecFromBExp, SpecQuant, SpecType, SpecUnOp
from selveri.spec.parser import parse_spec
from selveri.verifier.future_automaton import compile_future_automaton
from selveri.verifier.future_mapper import FutureLTLMapper
from selveri.verifier.models import FutureObligation
from selveri.verifier.z3_mapper import Z3Mapper


class VerificationEngine:

    SOLVER_TIMEOUT = 60000 # 1 minute timeout for each solver check.

    def __init__(self):
        self.last_step = 0
        self.declaration_steps: Dict[str, int] = dict() # stores the declaration steps of variable
        self.initialization_steps : Dict[str, int] = dict() # stores the initial assignment steps of variables
        self.history: list[RuntimeConfiguration] = list()
        self.pltl_memo: Dict[int, Dict[Spec, bool]] = dict() # for pLTL verification memoization
        self.fltl_pending: Dict[tuple[int, str], FutureObligation] = dict() # for future LTL verification pending obligations
        # (scope_id, spec_id) -> parsed spec; same id may resolve many times (e.g. loop conditions) if formula matches
        self.resolved_specs: Dict[tuple[int, Any], ParsedSpec] = dict()
        self.raw_specs: Dict[Any, RawSpec] = dict()

        # for past-LTL start markers
        self.pltl_start_marker_step: Dict[tuple[int, str], int] = dict()


    ################# Program Handling #################
    def handle_program_start(self) -> None:
        self.last_step = 0
        self.history.clear()
        self.pltl_memo.clear()
        self.fltl_pending.clear()
        self.pltl_start_marker_step.clear()
        self.resolved_specs.clear()

    def register_raw_specs(self, raw_specs: Iterable[RawSpec]) -> None:
        for raw_spec in raw_specs:
            self.raw_specs[raw_spec.spec_id] = raw_spec

    def handle_step(self, snapshot: RuntimeConfiguration) -> None:
        self.history.append(snapshot)
        self.pltl_memo[self.last_step] = dict()
        self.last_step += 1
        self.advance_future_obligations(snapshot)

    def resolve_spec(self, scope_id: int, spec_id: Any, formula_text: str) -> ParsedSpec: # resolve a spec
        key = (scope_id, spec_id)
        if key in self.resolved_specs:
            existing = self.resolved_specs[key]
            if existing.formula_text != formula_text:
                raise VerificationError(
                    f"Duplicate specification id {spec_id!r} in this scope with a different formula "
                    f"(was {existing.formula_text!r}, now {formula_text!r})."
                )
            return existing

        raw_spec = self.raw_specs.get(spec_id, RawSpec(spec_id=spec_id, formula_text=formula_text))
        try:
            spec_ast = parse_spec(formula_text)
        except ParserError as exc:
            raise VerificationError(
                f"Failed to parse specification {raw_spec.spec_id!r}: {exc.diagnostic.message}",
                code=DiagnosticCode.VERIFICATION_INVALID_SPECIFICATION,
                span=raw_spec.location,
                title="invalid specification",
                labels=(
                    (DiagnosticLabel(raw_spec.location, "specification could not be parsed", "primary"),)
                    if raw_spec.location is not None
                    else ()
                ),
                notes=(f"specification text: {formula_text}",),
            ) from None
        parsed = ParsedSpec(
            spec_id=spec_id,
            formula_text=formula_text,
            spec_type=spec_ast.spec_type,
            ast=spec_ast,
            location=raw_spec.location,
        )
        self.resolved_specs[key] = parsed
        return parsed

    # TODO: consider optimizations: updating the mapper at each IR assignment and declaration, then use push/pop instead of reset
    def handle_veri(self, spec_id: str, formula_text: str, snapshot: RuntimeConfiguration) -> None:
        parsed_spec = self.resolve_spec(snapshot.scope.scope_id, spec_id, formula_text)
        result = self.verify(parsed_spec, snapshot)
        if not result:
            self.raise_spec_failure(parsed_spec, "the current execution state does not satisfy the specification")

    def handle_plt_start_marker(self, spec_name: str, snapshot: RuntimeConfiguration) -> None:
        """Strict inclusive domain start for the past half of pLTL or sLTL (records ``last_step`` at this point)."""
        key = (snapshot.scope.scope_id, spec_name)
        if key in self.pltl_start_marker_step:
            raise SpecDomainBoundsError(f"Duplicate `{{ start {spec_name} }}` marker for this scope.")
        self.pltl_start_marker_step[key] = self.last_step

    def handle_flt_end_marker(self, spec_name: str, snapshot: RuntimeConfiguration) -> None:
        """
        Strict end for future-LTL or the future half of sLTL: verify one transition at this snapshot,
        require an accepting state, then drop the obligation (no further runtime checks for this spec).
        """
        obligation = self.fltl_pending.get((snapshot.scope.scope_id, spec_name))
        if obligation is None:
            raise VerificationError(
                f"No pending future-LTL obligation for specification {spec_name} at `{{ end ... }}`."
                f"This is due to either a missing {{ {spec_name} := ... }} or an explicit future-LTL domain end marker"
                "being later than the conservative automatic end bound."
            )
        self.advance_future_obligation(obligation, snapshot)
        if obligation.current_state not in obligation.automaton.accepting_states:
            self.raise_future_failure(
                obligation,
                f"The automaton did not end in an accepting state."
            )
        self.fltl_pending.pop((snapshot.scope.scope_id, spec_name))

    ################# SelVeri LP Extensions #################

    def check_spec_boolean(
        self,
        spec_id: str,
        formula_text: str,
        snapshot: RuntimeConfiguration,
    ) -> bool:
        """Evaluate a specification as a boolean value (VERIP instruction).
        Returns True if the spec is satisfied, False if not.
        Raises VerificationError only on Z3 'unknown' / timeout.
        """
        parsed_spec = self.resolve_spec(snapshot.scope.scope_id, spec_id, formula_text)
        if parsed_spec.spec_type in (SpecType.fLTL, SpecType.sLTL):
            raise VerificationError(
                f"Specification-booleans with {parsed_spec.spec_type.name} are not supported."
            )
        return self.verify(parsed_spec, snapshot)

    def extract_witness(
        self,
        var_name: str,
        spec_id: str,
        formula_text: str,
        snapshot: RuntimeConfiguration,
    ) -> Tuple[Any, Any]:
        """Extract a satisfying witness for bound variable 'var_name' under spec 'spec_id'.
        Returns (value, DeclType) on success.
        Raises IRRuntimeError on unsat/unknown (erroneous state per the formal semantics).

        Key insight: instead of mapping the full spec (which uses Z3 Exists and quantifies
        away the bound variable), we collect ALL quantifiers on the path to the target
        existential, register all their bound variables as free Z3 variables, and map the
        innermost body. This allows nested quantifiers like:
            Exists a in [1...10] . Exists b in [5,6,7,9] . &a * &a = &b
        """
        from selveri.common.errors import IRRuntimeError

        parsed_spec = self.resolve_spec(snapshot.scope.scope_id, spec_id, formula_text)
        spec = parsed_spec.ast
        lexical_depth = snapshot.scope.depth

        # Collect all quantifiers on the path to the target existential
        quant_chain: list = []
        innermost_body = self._collect_quant_chain(spec, var_name, quant_chain)
        if innermost_body is None:
            raise IRRuntimeError(
                f"obtain({var_name}, ...) failed: no existential quantifier binding "
                f"'{var_name}' found in the specification",
                code=DiagnosticCode.RUNTIME_OBTAIN_FAILED,
                span=parsed_spec.location,
                title="obtain failed",
                labels=(
                    (DiagnosticLabel(parsed_spec.location, f"`&{var_name}` is not bound by `Exists`", "primary"),)
                    if parsed_spec.location is not None
                    else ()
                ),
                hint=f"bind `{var_name}` with an existential quantifier in the specification",
            )

        solver = Solver()
        mapper = Z3Mapper(snapshot, solver, lexical_depth)

        # Register ALL bound variables in the chain as free Z3 variables
        for quant_spec in quant_chain:
            self._register_bound_var_as_free(quant_spec, mapper, solver)

        # Map the innermost body (with all bound vars accessible)
        z3_body = mapper.map_FOL(innermost_body)

        solver.set("timeout", VerificationEngine.SOLVER_TIMEOUT)
        result = solver.check(z3_body)

        if result != sat:
            raise IRRuntimeError(
                f"obtain({var_name}, ...) failed: no satisfying witness exists",
                code=DiagnosticCode.RUNTIME_OBTAIN_FAILED,
                span=parsed_spec.location,
                title="obtain failed",
                labels=(
                    (DiagnosticLabel(parsed_spec.location, "no value satisfies this obtain specification", "primary"),)
                    if parsed_spec.location is not None
                    else ()
                ),
                notes=(f"solver returned {result}", f"specification text: {parsed_spec.formula_text}"),
            )

        model = solver.model()

        # Extract the target bound variable's value from the model
        bound_z3_name = f"&{var_name}"
        for decl in model.decls():
            if decl.name() == bound_z3_name:
                z3_val = model[decl]
                return self._z3_value_to_python(z3_val)

        raise IRRuntimeError(
            f"obtain({var_name}, ...) failed: bound variable `&{var_name}` was not found in the solver model",
            code=DiagnosticCode.RUNTIME_OBTAIN_FAILED,
            span=parsed_spec.location,
            title="obtain failed",
            labels=(
                (DiagnosticLabel(parsed_spec.location, f"solver model did not include `&{var_name}`", "primary"),)
                if parsed_spec.location is not None
                else ()
            ),
            notes=(f"specification text: {parsed_spec.formula_text}",),
        )

    def _collect_quant_chain(
        self, spec: Spec, var_name: str, chain: list
    ) -> Any:
        """Walk the spec tree looking for Exists(var_name).
        Collect every SpecQuant encountered on the path into 'chain'.
        Returns the innermost body (the body of the target Exists), or None if not found.
        """
        if isinstance(spec, SpecQuant):
            chain.append(spec)
            if spec.kind == "Exists" and spec.var == var_name:
                return spec.body
            result = self._collect_quant_chain(spec.body, var_name, chain)
            if result is not None:
                return result
            chain.pop()   # backtrack if target not found in this branch
            return None
        if isinstance(spec, SpecBinOp):
            saved = len(chain)
            result = self._collect_quant_chain(spec.left, var_name, chain)
            if result is not None:
                return result
            del chain[saved:] # backtrack
            return self._collect_quant_chain(spec.right, var_name, chain)
        if isinstance(spec, SpecUnOp):
            return self._collect_quant_chain(spec.rhs, var_name, chain)
        return None

    def _register_bound_var_as_free(
        self, quant_spec: SpecQuant, mapper: Z3Mapper, solver: Any
    ) -> None:
        """Register a quantifier's bound variable as a free Z3 variable in the mapper,
        and add its domain constraints to the solver."""
        from selveri.common.errors import IRRuntimeError
        from selveri.spec.models import DomainType, DomainRange, DomainInterval, DomainValues
        from z3 import Int, Real, And, Or

        bound_var = quant_spec.var
        domain = quant_spec.domain
        bound_z3_name = f"&{bound_var}"

        if isinstance(domain, DomainType):
            if domain.ty.kind == "INT":
                bound_var_z3 = Int(bound_z3_name)
            elif domain.ty.kind == "REAL":
                bound_var_z3 = Real(bound_z3_name)
            else:
                raise IRRuntimeError(f"Unsupported domain type for obtain: {domain.ty.kind}")
        elif isinstance(domain, DomainRange):
            bound_var_z3 = Int(bound_z3_name)
            lo = mapper.map_FOL_aexp(domain.lo)
            hi = mapper.map_FOL_aexp(domain.hi)
            solver.add(And(lo <= bound_var_z3, bound_var_z3 <= hi))
        elif isinstance(domain, DomainInterval):
            bound_var_z3 = Real(bound_z3_name)
            lo = mapper.map_FOL_aexp(domain.lo)
            hi = mapper.map_FOL_aexp(domain.hi)
            lower = (lo <= bound_var_z3) if domain.left_closed else (lo < bound_var_z3)
            upper = (bound_var_z3 <= hi) if domain.right_closed else (bound_var_z3 < hi)
            solver.add(And(lower, upper))
        elif isinstance(domain, DomainValues):
            bound_var_z3 = Int(bound_z3_name)
            value_constraints = [bound_var_z3 == mapper.map_FOL_aexp(v) for v in domain.items]
            if value_constraints:
                solver.add(Or(value_constraints))
        else:
            # Fallback for other domains (DomainIdent, DomainVar)
            bound_var_z3 = Int(bound_z3_name)

        mapper.bound_var_map[bound_var] = bound_var_z3

    def _z3_value_to_python(self, z3_val: Any) -> Tuple[Any, Any]:
        """Convert a Z3 model value to a Python (value, DeclType) pair."""
        from selveri.common.runtime import DeclType
        from selveri.common.errors import IRRuntimeError
        from z3 import is_int_value, is_rational_value, is_true, is_false

        if is_int_value(z3_val):
            return (z3_val.as_long(), DeclType("INT", None, None))
        if is_rational_value(z3_val):
            return (real(z3_val.as_fraction()), DeclType("REAL", None, None))
        if is_true(z3_val) or is_false(z3_val):
            return (1 if is_true(z3_val) else 0, DeclType("INT", None, None))
        raise IRRuntimeError(f"Unsupported Z3 value type in obtain: {z3_val}")

    ################# ###########################    
    ################# Verifying #################
    #############################################        

    def verify(
        self,
        spec: ParsedSpec,
        snapshot: RuntimeConfiguration,
    ) -> bool:
        # lexical_depth captures the depth at which the spec is defined. This is passed to 
        # historical evaluations to ensure we don't resolve inner-scope variables that the spec shouldn't see.
        lexical_depth = snapshot.scope.depth
        sid = snapshot.scope.scope_id
        if spec.spec_type == SpecType.FOL:
            return self.verify_FOL(spec.ast, snapshot, lexical_depth)
        elif spec.spec_type == SpecType.pLTL:
            if (sid, spec.spec_id) in self.pltl_start_marker_step:
                start_step = self.pltl_start_marker_step.pop((sid, spec.spec_id))
            else:
                start_step = self.pltl_deduce_inital_step(spec.ast, snapshot.scope)
            return self.verify_past_LTL(spec.ast, self.last_step - 1, start_step, lexical_depth)
        elif spec.spec_type == SpecType.fLTL:
            return self.verify_future_LTL(spec, snapshot, lexical_depth)
        else: # spec.spec_type == SpecType.sLTL:
            if (sid, spec.spec_id) in self.pltl_start_marker_step:
                start_step = self.pltl_start_marker_step.pop((sid, spec.spec_id))
            else:
                start_step = self.pltl_deduce_inital_step(spec.ast, snapshot.scope)
            return self.verify_separated_LTL(spec, snapshot, start_step, lexical_depth)
    
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


    ################# sLTL Verification #################
    
    def verify_separated_LTL(self, spec: ParsedSpec, snapshot: RuntimeConfiguration, start_step: int, lexical_depth: int) -> bool:
        spec_ast_pltl = spec.ast.left
        spec_ast_fltl = spec.ast.right
        spec_fltl = ParsedSpec(spec_id=spec.spec_id, formula_text=spec.formula_text, spec_type=SpecType.fLTL, ast=spec_ast_fltl)

        if self.verify_past_LTL(spec_ast_pltl, self.last_step - 1, start_step, lexical_depth):
            return self.verify_future_LTL(spec_fltl, snapshot, lexical_depth)
        return True # vacous truth if past is false

    ################# pLTL Verification #################

    def verify_past_LTL(self, spec: Spec, step : int, start_step : int, lexical_depth: int = 0) -> bool:
        if spec in self.pltl_memo[step]: # memoization for efficiency
            return self.pltl_memo[step][spec]
        
        if spec.spec_type == SpecType.FOL:
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
            raise VerificationError(f"Unexpected specification type for pLTL: {spec.spec_type}")

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
        from selveri.high_level.ast import BNot, BBinOp, BCompare, BTruthy, BBool
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
        from selveri.high_level.ast import AVar, ALen, AIndex, AUnOp, ABinOp, IntLit, RealLit, FuncCall, ListLit
        from selveri.spec.parser import ABoundVar
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
        elif isinstance(aexp, ListLit):
            for item in aexp.items:
                self._aexp_get_free_variables(item, variables)
        # IntLit, RealLit — no variables

    def _domain_get_free_variables(self, domain: Any, variables: set[str]) -> None:
        from selveri.spec.models import DomainIdent, DomainValues, DomainRange, DomainInterval
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
        spec: ParsedSpec,
        snapshot: RuntimeConfiguration,
        lexical_depth: int,
    ) -> bool:
        '''
        Verify a future LTL specification.
        '''
        mapped_formula = FutureLTLMapper().map(spec.ast)
        automaton = compile_future_automaton(
            mapped_formula.formula_text,
            mapped_formula.atom_table.keys(),
        )
        obligation = FutureObligation(
            spec_id=spec.spec_id,
            source_spec=spec.formula_text,
            source_span=self.raw_specs.get(spec.spec_id).location if spec.spec_id in self.raw_specs else None,
            created_at_step=self.last_step,
            scope_id=snapshot.scope.scope_id,
            lexical_depth=lexical_depth,
            automaton=automaton,
            atom_table=mapped_formula.atom_table,
            current_state=automaton.initial_state,
            steps_to_skip=0,
        )
        self.advance_future_obligation(obligation, snapshot) # TODO: is it correct to directly move from the initial state?
        self.fltl_pending[(snapshot.scope.scope_id, spec.spec_id)] = obligation
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
        for obligation in self.fltl_pending.values():
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

    def on_scope_enter(self, _scope_id: int) -> None:
        """Hook when the interpreter enters a nested runtime scope (CSCOPE / FUNCENV)."""
        return

    def on_scope_exit(self, leaving_scope_id: int) -> None:
        """Hook when the interpreter exits a nested runtime scope (PSCOPE / RET)."""
        stale_plt = [k for k in self.pltl_start_marker_step if k[0] == leaving_scope_id]
        if stale_plt:
            names = ", ".join(repr(k[1]) for k in stale_plt)
            raise SpecDomainBoundsError(
                f"Unused past-LTL start marker(s) (no matching named specification in this scope): {names}"
            )

        self.fltl_pending = {
            key: obligation
            for key, obligation in self.fltl_pending.items()
            if key[0] != leaving_scope_id
        }

    def on_program_end(self, final_snapshot: RuntimeConfiguration) -> None:
        '''
        Check if the state reached by the future obligations is an accepting state.
        '''
        self.advance_future_obligations(final_snapshot)
        for obligation in self.fltl_pending.values():
            if obligation.current_state not in obligation.automaton.accepting_states:
                self.raise_future_failure(
                    obligation,
                    f"the execution ended in non-accepting automaton state {obligation.current_state}",
                )

    def raise_spec_failure(self, parsed_spec: ParsedSpec, detail: str) -> None:
        raw_spec = self.raw_specs.get(parsed_spec.spec_id)
        raise VerificationError(
            f"Specification {parsed_spec.spec_id} failed: {detail}.",
            span=raw_spec.location if raw_spec is not None else None,
            notes=(f"specification text: {parsed_spec.formula_text}",),
        )

    def raise_future_failure(self, obligation: FutureObligation, detail: str) -> None:
        raise VerificationError(
            f"Future specification {obligation.spec_id} failed: {detail}.",
            span=obligation.source_span,
            notes=(f"specification text: {obligation.source_spec}",),
        )
