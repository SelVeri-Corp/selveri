from __future__ import annotations

from enum import Enum
from typing import Any, Dict
from z3 import *

from ..runtime import Scope, State, _UNSET
from ..defs import RuntimeConfiguration
from ..specs import Spec, SpecFromBExp, SpecUnOp, SpecBinOp, SpecQuant, Domain, DomainIdent, DomainInterval, DomainRange, DomainType, DomainValues, DomainVar
from ..spec_parser import ABoundVar
from ..errors import VerifierRuntimeError
from ..parser import BExp, BBool, BNot, BBinOp, BCompare, BTruthy, AExp, IntLit, RealLit, ListLit, AVar, ALen, AIndex, AUnOp, ABinOp

class QuantifierType(Enum):
    Forall = 0
    Exists = 1


class Z3Mapper():

    free_var_map : Dict[str, z3types.Sort]
    bound_var_map : Dict[str, z3types.ExprRef]

    
    def __init__(self, config : RuntimeConfiguration, solver : Solver, lexical_depth: int = 0):
        self.free_var_map = dict()
        self.bound_var_map = dict()
        self.scope = config.scope
        self.state = config.state
        self.lexical_depth = lexical_depth

        # Note: this algorithm needs the state and scope to have the exact same depth.
        # This is ensured by the _fresh_runtime() method in the interpreter.
        if self.scope.depth != self.state.depth:
            raise VerifierRuntimeError(f"Scope depth and state depth mismatch")

        # map the scope and its parent scopes
        self.map_scope(self.scope)

        # map the state and its parent scopes
        self.map_state(self.state, self.scope, solver)


    ######### Configuration Mapping #########    

    def map_scope(self, scope: Scope) -> Dict[str, z3types.ExprRef]:
        for var, vartype in scope.types.items():  # local scope only; parents handled by recursion
            if vartype is None:
                continue
            
            var_z3_name = Z3Mapper.get_z3_var_name(var, scope.scope_id)
            if vartype.kind == "INT":
                self.free_var_map[var_z3_name] = Int(var_z3_name)
            elif vartype.kind == "REAL":
                self.free_var_map[var_z3_name] = Real(var_z3_name)
            elif vartype.kind == "LIST":
                if vartype.elem_kind == "INT":
                    self.free_var_map[var_z3_name] = Array(var_z3_name, IntSort(), IntSort())
                elif vartype.elem_kind == "REAL":
                    self.free_var_map[var_z3_name] = Array(var_z3_name, IntSort(), RealSort())
                else:
                    raise VerifierRuntimeError(f"Unsupported list element type: {vartype.elem_kind}")
            else:
                raise VerifierRuntimeError(f"Unsupported variable type: {vartype.kind}")

        # recurse to parent scopes
        if scope.parent is not None:
            self.map_scope(scope.parent)

    def map_state(self, state : State, scope: Scope, solver : Solver) -> None:
        for var, value in state.values.items():  # local state only; parents handled by recursion
            if value is _UNSET:
                continue
            if var not in scope.types:
                raise VerifierRuntimeError(f"Variable {var} not found in scope")
            try:
                vartype = scope.types.get(var)
                if vartype is None:
                    continue
                var_z3_name = Z3Mapper.get_z3_var_name(var, scope.scope_id)
                if vartype.kind == "LIST":
                    if len(value) != vartype.size:
                        raise VerifierRuntimeError(f"Variable {var} has size mismatch")
                    for i in range(vartype.size):
                        solver.add(self.free_var_map[var_z3_name][i] == value[i])
                else: # INT or REAL
                    solver.add(self.free_var_map[var_z3_name] == value)
            except Z3Exception as e:
                if "sort mismatch" in str(e):
                    raise VerifierRuntimeError(f"Variable {var} has sort mismatch")
                else:
                    raise
        
        if scope.parent is not None and state.parent is not None:
            self.map_state(state.parent, scope.parent, solver)
        elif scope.parent is None and state.parent is not None:
            raise VerifierRuntimeError(f"State has parent but scope does not")
        elif scope.parent is not None and state.parent is None:
            raise VerifierRuntimeError(f"Scope has parent but state does not")
                  

    
    ######### Specification Mapping #########    

    
    def map_FOL(self, spec: Spec) -> z3types.ExprRef:
        if isinstance(spec, SpecFromBExp):
            return self.map_FOL_bexp(spec)
        elif isinstance(spec, SpecUnOp):
            return self.map_FOL_unop(spec)
        elif isinstance(spec, SpecBinOp):
            return self.map_FOL_binop(spec)
        elif isinstance(spec, SpecQuant):
            return self.map_FOL_quant(spec)
        raise VerifierRuntimeError(f"Unsupported FOL specification")


    def map_FOL_aexp(self, aexp: AExp) -> z3types.ExprRef :
        if isinstance(aexp, IntLit):
            return IntVal(aexp.value)
        elif isinstance(aexp, RealLit):
            return RealVal(aexp.value)
        elif isinstance(aexp, ListLit):
            return self.map_FOL_listlit(aexp)
        elif isinstance(aexp, AVar):
            var_z3_name = self.get_scoped_z3_var_name(aexp.name)
            if var_z3_name not in self.free_var_map:
                raise VerifierRuntimeError(f"Free variable {aexp.name} (Z3 name: {var_z3_name}) not mapped")
            return self.free_var_map[var_z3_name]
        elif isinstance(aexp, ABoundVar):
            if aexp.name not in self.bound_var_map:
                raise VerifierRuntimeError(f"Bound variable &{aexp.name} not found in scope")
            return self.bound_var_map[aexp.name]
        elif isinstance(aexp, ALen):
            var_z3_name = self.get_scoped_z3_var_name(aexp.name)
            if var_z3_name not in self.free_var_map:
                raise VerifierRuntimeError(f"List variable {aexp.name} (Z3 name: {var_z3_name}) not mapped")
            if is_array(self.free_var_map[var_z3_name]): # LIST
                len_z3_name = self.get_length(aexp.name)
                if len_z3_name not in self.free_var_map:
                    raise VerifierRuntimeError(f"Length shadow variable {aexp.name}_len_1 of the list {aexp.name} (Z3 name: {var_z3_name}) not mapped")
                return self.free_var_map[len_z3_name]
            else: # INT or REAL
                return IntVal(0)
        elif isinstance(aexp, AIndex):
            return self.map_FOL_aindex(aexp)
        elif isinstance(aexp, AUnOp):
            if aexp.op == "-":
                return (-(self.map_FOL_aexp(aexp.rhs)))
            else:
                raise VerifierRuntimeError(f"Unsupported unary arithmetic operator: {aexp.op}")
        elif isinstance(aexp, ABinOp):
            if aexp.op == "+":
                return self.map_FOL_aexp(aexp.left) + self.map_FOL_aexp(aexp.right)
            elif aexp.op == "-":
                return self.map_FOL_aexp(aexp.left) - self.map_FOL_aexp(aexp.right)
            elif aexp.op == "*":
                return self.map_FOL_aexp(aexp.left) * self.map_FOL_aexp(aexp.right)
            elif aexp.op == "/":
                return self.map_FOL_aexp(aexp.left) / self.map_FOL_aexp(aexp.right)
            else:
                raise VerifierRuntimeError(f"Unsupported binary arithmetic operator: {aexp.op}")
        else:
            raise VerifierRuntimeError(f"Unsupported FOL arithmetic expression: {aexp}")

    def map_FOL_listlit(self, aexp: ListLit) -> z3types.ExprRef:
        stack: list = [aexp]
        flat_elems: list = []
        while stack:
            node = stack.pop()
            if isinstance(node, ListLit):
                for item in reversed(node.items):
                    stack.append(item)
            else:
                flat_elems.append(node)
        if not flat_elems:
            raise VerifierRuntimeError("Empty list literal is not supported in specifications")
        mapped = [self.map_FOL_aexp(x) for x in flat_elems]
        use_real = any(isinstance(x, RealLit) for x in flat_elems) or any(
            me.is_real() for me in mapped
        )
        if use_real:
            arr = K(IntSort(), RealVal(0))
            for i, me in enumerate(mapped):
                el = ToReal(me) if me.is_int() else me
                arr = Store(arr, IntVal(i), el)
        else:
            arr = K(IntSort(), IntVal(0))
            for i, me in enumerate(mapped):
                arr = Store(arr, IntVal(i), me)
        return arr

    def map_FOL_aindex(self, aexp: AIndex) -> z3types.ExprRef:
        indices: list[z3types.ExprRef] = []
        cur = aexp
        while isinstance(cur, AIndex): # flatten the index expression
            indices.append(self.map_FOL_aexp(cur.index))
            cur = cur.base
        base_name = cur.name
        base_z3_name = self.get_scoped_z3_var_name(base_name)
        if base_z3_name not in self.free_var_map:
            raise VerifierRuntimeError(f"Variable {base_name} (Z3 name: {base_z3_name}) not mapped")

        indices.reverse() # collect indices
        flat_index: z3types.ExprRef | None = None
        for pos, index_expr in enumerate(indices):
            term = index_expr
            for dim in range(pos + 2, self.get_list_dimension(base_name) + 1):
                len_name = self.get_dimension_length(base_name, dim)
                if len_name not in self.free_var_map:
                    raise VerifierRuntimeError(
                        f"Variable {base_name}_len_{dim} not found in scope"
                    )
                term = term * self.free_var_map[len_name]
            flat_index = term if flat_index is None else flat_index + term

        return self.free_var_map[base_z3_name][flat_index]

    def map_FOL_bexp(self, spec: SpecFromBExp) -> z3types.ExprRef:
        return self._map_bexp(spec.bexp)

    def _map_bexp(self, bexp: BExp) -> z3types.ExprRef:
        if isinstance(bexp, BBool):
            return BoolVal(bexp.value)
        elif isinstance(bexp, BNot):
            return Not(self._map_bexp(bexp.rhs))
        elif isinstance(bexp, BBinOp):
            if bexp.op == "and":
                return And(self._map_bexp(bexp.left), self._map_bexp(bexp.right))
            elif bexp.op == "or":
                return Or(self._map_bexp(bexp.left), self._map_bexp(bexp.right))
            elif bexp.op == "xor":
                return Xor(self._map_bexp(bexp.left), self._map_bexp(bexp.right))
            else:
                raise VerifierRuntimeError(f"Unsupported boolean operator: {bexp.op}")
        elif isinstance(bexp, BCompare):
            if bexp.op == "=":
                return (self.map_FOL_aexp(bexp.left) == self.map_FOL_aexp(bexp.right))
            elif bexp.op == "<":
                return (self.map_FOL_aexp(bexp.left) < self.map_FOL_aexp(bexp.right))
            elif bexp.op == "<=":
                return (self.map_FOL_aexp(bexp.left) <= self.map_FOL_aexp(bexp.right))
            elif bexp.op == ">":
                return (self.map_FOL_aexp(bexp.left) > self.map_FOL_aexp(bexp.right))
            elif bexp.op == ">=":
                return (self.map_FOL_aexp(bexp.left) >= self.map_FOL_aexp(bexp.right))
            else:
                raise VerifierRuntimeError(f"Unsupported comparison operator: {bexp.op}")
        elif isinstance(bexp, BTruthy):
            return (self.map_FOL_aexp(bexp.aexp) != 0)
        else:
            raise VerifierRuntimeError(f"Unsupported FOL boolean expression: {bexp}")
        

    def map_FOL_unop(self, spec: SpecUnOp) -> z3types.ExprRef:
        if spec.op == "!":
            return Not(self.map_FOL(spec.rhs))
        else:
            raise VerifierRuntimeError(f"Unsupported FOL unary operator: {spec.op}")

    def map_FOL_binop(self, spec: SpecBinOp) -> z3types.ExprRef:
        if spec.op == "&&":
            return And(self.map_FOL(spec.left), self.map_FOL(spec.right))
        elif spec.op == "||":
            return Or(self.map_FOL(spec.left), self.map_FOL(spec.right))
        elif spec.op == "=>":
            return Implies(self.map_FOL(spec.left), self.map_FOL(spec.right))
        else:
            raise VerifierRuntimeError(f"Unsupported FOL binary operator: {spec.op}")

    def map_FOL_quant(self, spec: SpecQuant) -> z3types.ExprRef:
        # Note: spec.var is the name of the bound variable
        #       and must be used in the body as &{name}
        if spec.kind == "Forall":
            return self.map_quantification(spec, QuantifierType.Forall)
        elif spec.kind == "Exists":
            return self.map_quantification(spec, QuantifierType.Exists)
        else:
            raise VerifierRuntimeError(f"Unsupported FOL quantifier: {spec.kind}")

    
    def map_quantification(self, spec : SpecQuant, quantifier : QuantifierType) -> z3types.ExprRef:
        if spec.var in self.bound_var_map:
            raise VerifierRuntimeError(f"Variable {spec.var} is already bound by a quantifier")
        
        domain : Domain = spec.domain
        bound_var = spec.var

        # uses Z3 quantifier
        if isinstance(domain, DomainType):
            if domain.ty.kind == "LIST":
                raise VerifierRuntimeError("List domain is not supported for Z3 quantifier")
            elif domain.ty.kind == "INT":
                bound_var_z3 = Int("&" + bound_var)
            elif domain.ty.kind == "REAL":
                bound_var_z3 = Real("&" + bound_var)
            else:
                raise VerifierRuntimeError(f"Unsupported domain type: {domain}")
            self.bound_var_map[bound_var] = bound_var_z3
            if quantifier == QuantifierType.Forall:
                result = ForAll(bound_var_z3, self.map_FOL(spec.body))
            else: # QuantifierType.Exists
                result = Exists(bound_var_z3, self.map_FOL(spec.body))
            self.bound_var_map.pop(bound_var, None)
            return result
        elif isinstance(domain, DomainRange):
            bound_var_z3 = Int("&" + bound_var)
            self.bound_var_map[bound_var] = bound_var_z3
            lo = self.map_FOL_aexp(domain.lo)
            hi = self.map_FOL_aexp(domain.hi)
            domain_z3 = And(lo <= bound_var_z3, bound_var_z3 <= hi)
            if quantifier == QuantifierType.Forall:
                result = ForAll(bound_var_z3, Implies(domain_z3, self.map_FOL(spec.body)))
            else: # QuantifierType.Exists
                result = Exists(bound_var_z3, And(domain_z3, self.map_FOL(spec.body)))
            self.bound_var_map.pop(bound_var, None)
            return result
        elif isinstance(domain, DomainInterval):
            bound_var_z3 = Real("&" + bound_var)
            self.bound_var_map[bound_var] = bound_var_z3
            lo = self.map_FOL_aexp(domain.lo)
            hi = self.map_FOL_aexp(domain.hi)
            if domain.left_closed:
                lower_endpoint = (lo <= bound_var_z3)
            else:
                lower_endpoint = (lo < bound_var_z3)
            if domain.right_closed:
                upper_endpoint = (bound_var_z3 <= hi)
            else:
                upper_endpoint = (bound_var_z3 < hi)
            domain_z3 = And(lower_endpoint, upper_endpoint)
            if quantifier == QuantifierType.Forall:
                result = ForAll(bound_var_z3, Implies(domain_z3, self.map_FOL(spec.body)))
            else: # QuantifierType.Exists
                result = Exists(bound_var_z3, And(domain_z3, self.map_FOL(spec.body)))
            self.bound_var_map.pop(bound_var, None)
            return result
        else: # uses finite_connector over finite domain
            result_list = list()
            if isinstance(domain, DomainIdent):
                domain_z3_name = self.get_scoped_z3_var_name(domain.name)
                if domain_z3_name not in self.free_var_map:
                    raise VerifierRuntimeError(f"Variable {domain.name} (Z3 name: {domain_z3_name}) not mapped")
                if not is_array(self.free_var_map[domain_z3_name]):
                    raise VerifierRuntimeError(f"Variable {domain.name} is not a list")
                # Use scope-aware type resolution for list length
                owner_scope = self._get_lexical_scope().find_owner(domain.name)
                if owner_scope is None:
                    raise VerifierRuntimeError(f"List {domain.name} not found in lexical scope")
                list_type = owner_scope.types.get(domain.name)
                if list_type is None or list_type.kind != "LIST" or list_type.size is None:
                    raise VerifierRuntimeError(f"Cannot determine size of list {domain.name}")
                for i in range(list_type.size):
                    self.bound_var_map[bound_var] = self.map_FOL_aexp(AIndex(AVar(domain.name), IntLit(i)))
                    result_list.append(self.map_FOL(spec.body))     
            elif isinstance(domain, DomainValues):
                for val in domain.items:
                    self.bound_var_map[bound_var] = self.map_FOL_aexp(val)
                    result_list.append(self.map_FOL(spec.body))     
            elif isinstance(domain, DomainVar):
                lex_scope = self._get_lexical_scope()
                for var, vartype in lex_scope.items():
                    if vartype == domain.elem:
                        owner = lex_scope.find_owner(var)
                        if owner is not None:
                            self.bound_var_map[bound_var] = self.free_var_map[Z3Mapper.get_z3_var_name(var, owner.scope_id)]
                            result_list.append(self.map_FOL(spec.body))   
                        else:
                            raise VerifierRuntimeError(f"Variable {var} not found in scope")               
            else:
                raise VerifierRuntimeError(f"Unsupported domain type: {domain}")
                
            self.bound_var_map.pop(bound_var, None)
            if quantifier == QuantifierType.Forall:
                return And(result_list)
            else: # QuantifierType.Exists
                return Or(result_list)
            

    ######## Utility Methods ########

    def get_list_dimension(self, name: str) -> int:
        owner = self.scope.find_owner(name)
        if owner is None:
            raise VerifierRuntimeError(f"Variable {name} not found in scope")
        name_z3 = Z3Mapper.get_z3_var_name(name, owner.scope_id)
        if name_z3 not in self.free_var_map or self.free_var_map[name_z3] is None:
            raise VerifierRuntimeError(f"Variable {name} not found in scope")
        if not is_array(self.free_var_map[name_z3]):
            raise VerifierRuntimeError(f"Variable {name} is not a list")

        dim = 0
        while self.get_dimension_length(name, dim + 1) in self.free_var_map:
            dim += 1
        return dim if dim > 0 else 1

    def get_length(self, var_name: str) -> str:
        return self.get_dimension_length(var_name, 1)

    def get_dimension_length(self, var_name: str, dim: int) -> str:
        return Z3Mapper.get_z3_var_name(f"_{var_name}_len_{dim}", self.get_lexical_owner_scope_id(var_name))
    
    def _get_lexical_scope(self) -> Scope:
        """
        Traverses up the scope hierarchy to find the scope matching the current lexical depth.
        This ensures we don't accidentally resolve variables from inner scopes that were not visible
        when/where the specification was defined (vertical isolation).
        """
        lex_scope = self.scope
        while lex_scope is not None and lex_scope.depth > self.lexical_depth:
            lex_scope = lex_scope.parent
        if lex_scope is None:
            raise VerifierRuntimeError(f"Lexical depth {self.lexical_depth} not found")
        return lex_scope

    def get_lexical_owner_scope_id(self, name: str) -> int:
        lex_scope = self._get_lexical_scope()
        owner = lex_scope.find_owner(name)
        if owner is None:
            raise VerifierRuntimeError(f"Variable {name} not found in lexical scope")
        return owner.scope_id

    def get_scoped_z3_var_name(self, name: str) -> str:
        """
        Resolves the lexical owner scope of a given variable name and returns its fully qualified
        Z3 variable name, ensuring accurate mapping to the correct isolated execution context.
        """
        return Z3Mapper.get_z3_var_name(name, self.get_lexical_owner_scope_id(name))

    @staticmethod
    def get_z3_var_name(var_name: str, scope_id : int) -> str:
        # scope_id gives every unique scope block its own globally unique Z3 namespace,
        # ensuring independent history tracking for horizontally isolated blocks.
        return f"_s{scope_id}_{var_name}"
