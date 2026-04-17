from __future__ import annotations

from dataclasses import dataclass
from itertools import count
from pathlib import Path

from lark import Lark, LarkError, Transformer, v_args

from .errors import ParserError, VerifierRuntimeError
from .parser import (
    ABinOp,
    ALen,
    AExp,
    AIndex,
    AUnOp,
    AVar,
    BBinOp,
    BBool,
    BCompare,
    BExp,
    BNot,
    BTruthy,
    BasicType,
    FloatLit,
    IntLit,
    ListLit,
    TypeFloat,
    TypeInt,
    TypeList,
)
from .specs import (
    DomainIdent,
    DomainInterval,
    DomainRange,
    DomainType,
    DomainValues,
    DomainVar,
    Spec,
    SpecBinOp,
    SpecFromBExp,
    SpecQuant,
    SpecUnOp,
    SpecType
)

@dataclass(frozen=True)
class ABoundVar(AExp):
    name: str

    def __str__(self) -> str:
        return f"&{self.name}"

# Keep formula-node identities distinct even across separate parse_spec calls.
_SPEC_NODE_UIDS = count()


@v_args(inline=True)
class SpecAstBuilder(Transformer):
    def _next_uid(self) -> int:
        return next(_SPEC_NODE_UIDS)

    def start(self, spec: Spec) -> Spec: return spec

    # types
    def type_int(self) -> TypeInt: return TypeInt()

    def type_float(self) -> TypeFloat: return TypeFloat()

    def type_list(self, elem: BasicType, dimension, shape): return TypeList(elem, IntLit(int(dimension)), shape)

    def aexp_list(self, *items): return list(items)
    def aexp(self, expr): return expr
    def int_lit(self, tok): return IntLit(int(tok))
    def float_lit(self, tok): return FloatLit(float(tok))
    def list_lit(self, *args): return ListLit(list(args))
    def a_var(self, name): return AVar(str(name))
    def a_bound_var(self, name): return ABoundVar(str(name))

    def a_len(self, name): return ALen(str(name))
    def a_index(self, base, idx): 
        if not isinstance(base, AExp): 
            base = AVar(str(base)) 
        return AIndex(base, idx)  
    def neg(self, rhs): return AUnOp("-", rhs)
    def add(self, l, r): return ABinOp("+", l, r)
    def sub(self, l, r): return ABinOp("-", l, r)
    def mul(self, l, r): return ABinOp("*", l, r)
    def div(self, l, r): return ABinOp("/", l, r)

    # boolean
    def bexp(self, expr): return expr
    def btrue(self): return BBool(True)
    def bfalse(self): return BBool(False)
    def bnot(self, rhs): return BNot(rhs)
    def band(self, l, r): return BBinOp("and", l, r)
    def bxor(self, l, r): return BBinOp("xor", l, r)
    def bor(self, l, r): return BBinOp("or", l, r)
    def compare(self, l, op, r): return BCompare(str(op), l, r)
    def truthy(self, aexp): return BTruthy(aexp)

    # specs
    def sbexp(self, bexp: BExp): return SpecFromBExp(self._next_uid(), SpecType.FOL, bexp) # TODO: can it have a type other than FOL?
    def snot(self, rhs: Spec): return SpecUnOp(self._next_uid(), rhs.type, "!", rhs)
    def spreviously(self, rhs: Spec): return SpecUnOp(self._next_uid(), SpecType.pLTL, "Previously", rhs)
    def sonce(self, rhs: Spec): return SpecUnOp(self._next_uid(), SpecType.pLTL, "Once", rhs)
    def shistorically(self, rhs: Spec): return SpecUnOp(self._next_uid(), SpecType.pLTL, "Historically", rhs)
    def snext(self, rhs: Spec): return SpecUnOp(self._next_uid(), SpecType.fLTL, "Next", rhs)
    def seventually(self, rhs: Spec): return SpecUnOp(self._next_uid(), SpecType.fLTL, "Eventually", rhs)
    def salways(self, rhs: Spec): return SpecUnOp(self._next_uid(), SpecType.fLTL, "Always", rhs)
    def ssince(self, l: Spec, r: Spec): return SpecBinOp(self._next_uid(), self.deduce_spec_type(l,r), "Since", l, r)
    def suntil(self, l: Spec, r: Spec): return SpecBinOp(self._next_uid(), self.deduce_spec_type(l,r), "Until", l, r)
    def sand(self, l: Spec, r: Spec): return SpecBinOp(self._next_uid(), self.deduce_spec_type(l,r), "&&", l, r)
    def sor(self, l: Spec, r: Spec): return SpecBinOp(self._next_uid(), self.deduce_spec_type(l,r), "||", l, r)
    def simp(self, l: Spec, r: Spec): return SpecBinOp(self._next_uid(), "=>", self.deduce_spec_type(l,r), l, r)
    # state_spec subgrammar (non-temporal quantified bodies; same AST as temporal-level operators)
    def state_snot(self, rhs: Spec): return self.snot(rhs)
    def state_sand(self, l: Spec, r: Spec): return self.sand(l, r)
    def state_sor(self, l: Spec, r: Spec): return self.sor(l, r)
    def state_simp(self, l: Spec, r: Spec): return self.simp(l, r)
    def state_sbexp(self, bexp: BExp): return self.sbexp(bexp)
    def state_forall(self, var, domain, body): return self.sforall(var, domain, body)
    def state_exists(self, var, domain, body): return self.sexists(var, domain, body)
    # domains
    def domain_ident(self, name): return DomainIdent(str(name))
    def domain_range(self, lo, hi): return DomainRange(IntLit(int(lo)), IntLit(int(hi)))
    def domain_values(self, lit: ListLit): return DomainValues(list(lit.items))
    def domain_type(self, type_node: BasicType): return DomainType(type_node)
    def domain_var(self, elem_ty: BasicType): return DomainVar(elem_ty)
    def interval_closed(self, lo, hi):
        return DomainInterval(
            FloatLit(float(lo)), FloatLit(float(hi)), left_closed=True, right_closed=True
        )
    def interval_open(self, lo, hi):
        return DomainInterval(
            FloatLit(float(lo)), FloatLit(float(hi)), left_closed=False, right_closed=False
        )
    def interval_halfopen(self, lo, hi):
        return DomainInterval(
            FloatLit(float(lo)), FloatLit(float(hi)), left_closed=True, right_closed=False
        )
    def interval_halfclosed(self, lo, hi):
        return DomainInterval(
            FloatLit(float(lo)), FloatLit(float(hi)), left_closed=False, right_closed=True
        )
    def sforall(self, var, domain, body): 
        if body.type != SpecType.FOL:
            return VerifierRuntimeError("LTL formulae cannot be quantified!")
        return SpecQuant(self._next_uid(), SpecType.FOL, "Forall", str(var), domain, body)

    def sexists(self, var, domain, body): 
        if body.type != SpecType.FOL:
            return VerifierRuntimeError("LTL formulae cannot be quantified!")
        return SpecQuant(self._next_uid(), SpecType.FOL, "Exists", str(var), domain, body)


    def deduce_spec_type(spec1 : Spec, spec2: Spec) -> SpecType:
        if spec1.type == spec2.type:
            return spec1.type
        if spec1.type == SpecType.FOL:
            return spec2.type
        if spec2.type == SpecType.FOL:
            return spec2.type
        return VerifierRuntimeError("Mixed LTL formulae are not allowed!")


SPEC_PARSER = Lark.open(
    str(Path(__file__).resolve().parent / "grammars" / "spec_grammar.lark"),
    parser="lalr",
    lexer="contextual",
    maybe_placeholders=False,
)


def parse_spec(src: str) -> Spec:
    try:
        tree = SPEC_PARSER.parse(src)
    except LarkError as e:
        raise ParserError("Failed to parse SelVeri specification. " + str(e)) from None
    return SpecAstBuilder().transform(tree)
    