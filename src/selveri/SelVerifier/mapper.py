from __future__ import annotations

from enum import Enum
from typing import Any, Dict
from z3 import *

from ..defs import RuntimeConfiguration
from ..specs import Spec, SpecFromBExp, SpecUnOp, SpecBinOp, SpecQuant, Domain, DomainIdent, DomainInterval, DomainRange, DomainType, DomainValues, DomainVar
from ..spec_parser import ABoundVar
from ..errors import VerifierRuntimeError
from ..parser import BExp, BBool, BNot, BBinOp, BCompare, BTruthy, AExp, IntLit, FloatLit, ListLit, AVar, ALen, AIndex, AUnOp, ABinOp

class QuantifierType(Enum):
    Forall = 0
    Exists = 1


class Z3Mapper():

    var_map : Dict[str, z3types.Sort]
    bound_vars_map : Dict[str, z3types.ExprRef] = dict()

    
    def __init__(self, config : RuntimeConfiguration, solver : Solver):
        self.var_map = dict()
        self.scope = config.scope
        self.state = config.state

        # map the scope
        # TODO: does this also handle parent scopes and states?

        for var, vartype in self.scope.items():
            if vartype is None:
                continue
            if vartype.kind == "INT":
                self.var_map[var] = Int(var)
            elif vartype.kind == "FLOAT":
                self.var_map[var] = Real(var)
            elif vartype.kind == "LIST":
                if vartype.elem_kind == "INT":
                    self.var_map[var] = Array(var, IntSort(), IntSort())
                elif vartype.elem_kind == "FLOAT":
                    self.var_map[var] = Array(var, IntSort(), RealSort())
                else:
                    raise VerifierRuntimeError(f"Unsupported list element type: {vartype.elem_kind}")
            else:
                raise VerifierRuntimeError(f"Unsupported variable type: {vartype.kind}")

        for var, value in self.state.items():
            if var not in self.scope:
                raise VerifierRuntimeError(f"Variable {var} not found in scope")
            try:
                vartype = self.scope[var]
                if vartype is None:
                    continue
                if vartype.kind == "LIST":
                    if len(value) != vartype.size:
                        raise VerifierRuntimeError(f"Variable {var} has size mismatch")
                    for i in range(vartype.size):
                        solver.add(self.var_map[var][i] == value[i])
                else: # INT or FLOAT
                    solver.add(self.var_map[var] == value)
            except Z3Exception as e:
                if "sort mismatch" in str(e):
                    raise VerifierRuntimeError(f"Variable {var} has sort mismatch")
                else:
                    raise

        self.temp_count = 0
    
    def map_FOL(self , spec: Spec) -> z3types.ExprRef:
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
        elif isinstance(aexp, FloatLit):
            return RealVal(aexp.value)
        elif isinstance(aexp, ListLit):
            return self.map_FOL_listlit(aexp)
        elif isinstance(aexp, AVar):
            if aexp.name not in self.var_map:
                raise VerifierRuntimeError(f"Free variable {aexp.name} not found in scope")
            return self.var_map[aexp.name]
        elif isinstance(aexp, ABoundVar):
            if aexp.name not in self.bound_vars_map:
                raise VerifierRuntimeError(f"Bound variable &{aexp.name} not found in scope")
            return self.bound_vars_map[aexp.name]
        elif isinstance(aexp, ALen):
            if aexp.name not in self.var_map:
                raise VerifierRuntimeError(f"List variable {aexp.name} not found in scope")
            if is_array(self.var_map[aexp.name]): # LIST
                len_name = Z3Mapper.get_length(aexp.name)
                if len_name not in self.var_map:
                    raise VerifierRuntimeError(f"Length shadow variable {len_name} of the list {aexp.name} not found in scope")
                return self.var_map[len_name]
            else: # INT or FLOAT
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
        flat_imms: list = []
        while stack:
            node = stack.pop()
            if isinstance(node, ListLit):
                for item in reversed(node.items):
                    stack.append(item)
            elif isinstance(node, IntLit):
                flat_imms.append(node)
            elif isinstance(node, FloatLit):
                flat_imms.append(node)
            else:
                raise VerifierRuntimeError(f"Unsupported list literal element: {node}")
        if not flat_imms:
            raise VerifierRuntimeError("Empty list literal is not supported in specifications")
        use_real = any(isinstance(x, FloatLit) for x in flat_imms)
        if use_real:
            arr = K(IntSort(), RealVal(0))
            for i, imm in enumerate(flat_imms):
                arr = Store(arr, IntVal(i), RealVal(imm.value))
        else:
            arr = K(IntSort(), IntVal(0))
            for i, imm in enumerate(flat_imms):
                arr = Store(arr, IntVal(i), IntVal(imm.value))
        return arr

    def map_FOL_aindex(self, aexp: AIndex) -> z3types.ExprRef:
        indices: list[z3types.ExprRef] = []
        cur = aexp
        while isinstance(cur, AIndex): # flatten the index expression
            indices.append(self.map_FOL_aexp(cur.index))
            cur = cur.base
        base_name = cur.name
        if base_name not in self.var_map:
            raise VerifierRuntimeError(f"Variable {base_name} not found in scope")

        indices.reverse() # collect indices
        flat_index: z3types.ExprRef | None = None
        for pos, index_expr in enumerate(indices):
            term = index_expr
            for dim in range(pos + 2, self.get_list_dimension(base_name) + 1):
                len_name = Z3Mapper.get_dimension_length(base_name, dim)
                if len_name not in self.var_map:
                    raise VerifierRuntimeError(
                        f"Variable {len_name} not found in scope"
                    )
                term = term * self.var_map[len_name]
            flat_index = term if flat_index is None else flat_index + term

        return self.var_map[base_name][flat_index]

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
        if spec.var in self.bound_vars_map:
            raise VerifierRuntimeError(f"Variable {spec.var} is already bound by a quantifier")
        
        domain : Domain = spec.domain
        bound_var = spec.var


        # uses Z3 quantifier
        if isinstance(domain, DomainType):
            if domain.ty.kind == "LIST":
                raise VerifierRuntimeError("List domain is not supported for Z3 quantifier")
            elif domain.ty.kind == "INT":
                bound_var_z3 = Int("&" + bound_var)
            elif domain.ty.kind == "FLOAT":
                bound_var_z3 = Real("&" + bound_var)
            else:
                raise VerifierRuntimeError(f"Unsupported domain type: {domain}")
            self.bound_vars_map[bound_var] = bound_var_z3
            if quantifier == QuantifierType.Forall:
                result = ForAll(bound_var_z3, self.map_FOL(spec.body))
            else: # QuantifierType.Exists
                result = Exists(bound_var_z3, self.map_FOL(spec.body))
            del self.bound_vars_map[bound_var]
            return result
        elif isinstance(domain, DomainRange):
            bound_var_z3 = Int("&" + bound_var)
            self.bound_vars_map[bound_var] = bound_var_z3
            lo = self.map_FOL_aexp(domain.lo)
            hi = self.map_FOL_aexp(domain.hi)
            domain_z3 = And(lo <= bound_var_z3, bound_var_z3 <= hi)
            if quantifier == QuantifierType.Forall:
                result = ForAll(bound_var_z3, Implies(domain_z3, self.map_FOL(spec.body)))
            else: # QuantifierType.Exists
                result = Exists(bound_var_z3, And(domain_z3, self.map_FOL(spec.body)))
            del self.bound_vars_map[bound_var]
            return result
        elif isinstance(domain, DomainInterval):
            bound_var_z3 = Real("&" + bound_var)
            self.bound_vars_map[bound_var] = bound_var_z3
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
            del self.bound_vars_map[bound_var]
            return result
        else: # uses finite_connector over finite domain
            result_list = list()
            if isinstance(domain, DomainIdent):
                if not is_array(self.var_map[domain.name]):
                    raise VerifierRuntimeError(f"Variable {domain.name} is not a list")
                # TODO: consider multi-dim lists
                for i in range(self.state[Z3Mapper.get_dimension_length(domain.name, 1)]):
                    self.bound_vars_map[bound_var] = self.map_FOL_aexp(AIndex(AVar(domain.name), IntLit(i)))
                    result_list.append(self.map_FOL(spec.body))     
            elif isinstance(domain, DomainValues):
                for val in domain.items:
                    self.bound_vars_map[bound_var] = self.map_FOL_aexp(val)
                    result_list.append(self.map_FOL(spec.body))     
            elif isinstance(domain, DomainVar):
                for var, vartype in self.scope.items():
                    if vartype == domain.elem:
                        self.bound_vars_map[bound_var] = self.var_map[var]
                        result_list.append(self.map_FOL(spec.body))                  
            else:
                raise VerifierRuntimeError(f"Unsupported domain type: {domain}")
                
            del self.bound_vars_map[bound_var]
            if quantifier == QuantifierType.Forall:
                return And(result_list)
            else: # QuantifierType.Exists
                return Or(result_list)
            

                
    def get_list_dimension(self, name: str) -> int:
        if name not in self.var_map or self.var_map[name] is None:
            raise VerifierRuntimeError(f"Variable {name} not found in scope")
        if not is_array(self.var_map[name]):
            raise VerifierRuntimeError(f"Variable {name} is not a list")

        dim = 0
        while Z3Mapper.get_dimension_length(name, dim + 1) in self.var_map:
            dim += 1
        return dim if dim > 0 else 1

    @staticmethod
    def get_length(var_name: str) -> str:
        return Z3Mapper.get_dimension_length(var_name, 1)

    @staticmethod
    def get_dimension_length(var_name: str, dim: int) -> str:
        return f"_{var_name}_len_{dim}"
            
        
        
            
            

                
            
        
