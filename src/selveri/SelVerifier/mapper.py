from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict
from z3 import *

from .defs import RuntimeConfiguration
from ..specs import Spec, SpecFromBExp, SpecUnOp, SpecBinOp, SpecQuant
from ..errors import VerifierRuntimeError
from ..parser import BExp, BBool, BNot, BBinOp, BCompare, BTruthy, AExp, IntLit, FloatLit, ListLit, AVar, ALen, AIndex, AUnOp, ABinOp

@dataclass
class Z3Mapper():

    var_map : Dict[str, z3types.Sort]
    
    def __init__(self, config : RuntimeConfiguration, solver : Solver):
        self.var_map = {}
        scope = config.scope
        state = config.state

        # map the scope
        # TODO: does this also handle parent scopes and states?

        for var, vartype in scope.items():
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

        for var, value in state.items():
            if var not in scope:
                raise VerifierRuntimeError(f"Variable {var} not found in scope")
            try:
                vartype = scope[var]
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
                raise VerifierRuntimeError(f"Variable {aexp.name} not found in scope")
            return self.var_map[aexp.name]
        elif isinstance(aexp, ALen):
            if aexp.name not in self.var_map:
                raise VerifierRuntimeError(f"Variable {aexp.name} not found in scope")
            if is_array(self.var_map[aexp.name]): # LIST
                len_name = Z3Mapper.get_length(aexp.name)
                if len_name not in self.var_map:
                    raise VerifierRuntimeError(f"Variable {len_name} not found in scope")
                return self.var_map[len_name]
            else: # INT or FLOAT
                return IntVal(0)
        elif isinstance(aexp, AIndex):
            pass # TODO: handle list-flattening here
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


    def map_FOL_bexp(self, spec: SpecFromBExp) -> z3types.ExprRef :
        bexp = spec.bexp
        if isinstance(bexp, BBool):
            return BoolVal(bexp.value)
        elif isinstance(bexp, BNot):
            return Not(self.map_FOL_bexp(bexp.rhs))
        elif isinstance(bexp, BBinOp):
            if bexp.op == "and":
                return And(self.map_FOL_bexp(bexp.left), self.map_FOL_bexp(bexp.right))
            elif bexp.op == "or":
                return Or(self.map_FOL_bexp(bexp.left), self.map_FOL_bexp(bexp.right))
            elif bexp.op == "xor":
                return Xor(self.map_FOL_bexp(bexp.left), self.map_FOL_bexp(bexp.right))
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
            return And(self.map_FOL(spec.lhs), self.map_FOL(spec.rhs))
        elif spec.op == "||":
            return Or(self.map_FOL(spec.lhs), self.map_FOL(spec.rhs))
        elif spec.op == "=>":
            return Implies(self.map_FOL(spec.lhs), self.map_FOL(spec.rhs))
        else:
            raise VerifierRuntimeError(f"Unsupported FOL binary operator: {spec.op}")

    # TODO: implement after the 'domain' change
    def map_FOL_quant(self, spec: SpecQuant) -> z3types.ExprRef:
        if spec.king == "Forall":
            pass
        elif spec.kind == "Exists":
            pass
        else:
            raise VerifierRuntimeError(f"Unsupported FOL quantifier: {spec.kind}")

    def generate_temp(self) -> str:
        temp = "_temp_" + str(self.temp_count)
        self.temp_count += 1
        return temp

    @staticmethod
    def get_length(self, var_name: str) -> str:
        return "_" + var_name + "_len_1"
        
    


        
            
        
        
            
            

                
            
        
