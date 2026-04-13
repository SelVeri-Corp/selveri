from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from lark import Lark, LarkError, Token, Transformer, v_args

from .errors import ParserError
from .specs import RawSpec
from .preprocessor import extract_raw_specs

# =========================
# AST
# =========================
@dataclass(frozen=True)
class Program:
    func_decls: List["FunctionDecl"]
    stmt_seq: List["Stmt"]

class Stmt: pass
class AExp: pass
class BExp: pass
class TypeNode: 
    def __str__(self) -> str:
        raise NotImplementedError("Subclasses must implement __str__")
class ConcreteType(TypeNode):
    def __str__(self) -> str:
        raise NotImplementedError("Subclasses must implement __str__")
class Imm:
    def __str__(self) -> str:
        raise NotImplementedError("Subclasses must implement __str__")

# Types
class BasicType(ConcreteType):
    def __str__(self) -> str:
        raise NotImplementedError("Subclasses must implement __str__")

@dataclass(frozen=True)
class TypeInt(BasicType):
    def __str__(self) -> str:
        return "INT"

@dataclass(frozen=True)
class TypeFloat(BasicType):
    def __str__(self) -> str:
        return "FLOAT"

@dataclass(frozen=True)
class TypeList(ConcreteType):
    elem: BasicType
    dimension: "IntLit"
    shape: List[AExp] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.dimension.value != len(self.shape):
            raise ParserError(
                f"List type dimension ({self.dimension.value}) does not match "
                f"declared shape count ({len(self.shape)})."
            )

    def __str__(self) -> str:
        return f"LIST[{self.elem},{self.dimension}{f', {self.shape}' if self.shape else ''}]"

@dataclass(frozen=True)
class TypeDynamicList(TypeNode):
    elem: BasicType
    dimension: "IntLit"
    shape: List[Optional[AExp]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if len(self.shape) > self.dimension.value:
            raise ParserError(
                f"Dynamic list type dimension ({self.dimension.value}) cannot be smaller "
                f"than declared shape count ({len(self.shape)})."
            )

    def __str__(self) -> str:
        return f"LIST[{self.elem}, {self.dimension}{f', {self.shape}' if self.shape else ''}]"

# Immediates / Arithmetic
@dataclass(frozen=True)
class IntLit(Imm, AExp):
    value: int

    def __str__(self) -> str:
        return str(self.value)

@dataclass(frozen=True)
class FloatLit(Imm, AExp):
    value: float
    def __str__(self) -> str:
        return str(self.value)

# TODO: we might remove this
@dataclass(frozen=True)
class ListLit(Imm, AExp):
    items: List[Imm]

@dataclass(frozen=True)
class AVar(AExp):
    name: str

    def __str__(self) -> str:
        return self.name

@dataclass(frozen=True)
class ALen(AExp):
    name: str

    def __str__(self) -> str:
        return f"len({self.name})"

@dataclass(frozen=True)
class AIndex(AExp):
    base: AExp
    index: AExp

    def __str__(self) -> str:
        return f"{self.base}[{self.index}]"

@dataclass(frozen=True)
class AUnOp(AExp):
    op: str
    rhs: AExp

    def __str__(self) -> str:
        return f"{self.op}{self.rhs}"

@dataclass(frozen=True)
class ABinOp(AExp):
    op: str
    left: AExp
    right: AExp

    def __str__(self) -> str:
        return f"{self.left} {self.op} {self.right}"

# Boolean
@dataclass(frozen=True)
class BBool(BExp):
    value: bool

    def __str__(self) -> str:
        return "true" if self.value else "false"

@dataclass(frozen=True)
class BNot(BExp):
    rhs: BExp

    def __str__(self) -> str:
        return f"!{self.rhs}"

@dataclass(frozen=True)
class BBinOp(BExp):
    op: str
    left: BExp
    right: BExp

    def __str__(self) -> str:
        return f"{self.left} {self.op} {self.right}"

@dataclass(frozen=True)
class BCompare(BExp):
    op: str
    left: AExp
    right: AExp

    def __str__(self) -> str:
        return f"{self.left} {self.op} {self.right}"

@dataclass(frozen=True)
class BTruthy(BExp):
    aexp: AExp


# Statements
@dataclass(frozen=True)
class Decl(Stmt):
    name: str
    type_node: TypeNode

@dataclass(frozen=True)
class Assign(Stmt):
    name: str
    aexp: AExp

@dataclass(frozen=True)
class ListAssign(Stmt):
    target: AIndex
    aexp: AExp

@dataclass(frozen=True)
class Pass(Stmt): pass

@dataclass(frozen=True)
class SpecAnnot(Stmt):
    spec: RawSpec

@dataclass(frozen=True)
class If(Stmt):
    cond: BExp
    then_s: List[Stmt]
    else_s: Optional[List[Stmt]]

@dataclass(frozen=True)
class While(Stmt):
    cond: BExp
    body: List[Stmt]

# Functions
@dataclass(frozen=True)
class Param:
    name: str
    type_node: TypeNode

@dataclass(frozen=True)
class Return(Stmt):
    value: AExp

@dataclass(frozen=True)
class FuncCall(AExp, Stmt):
    name: str
    args: List[AExp]

@dataclass(frozen=True)
class FunctionDecl:
    name: str
    params: List[Param]
    return_type: TypeNode
    body: List[Stmt]

@v_args(inline=True)
class AstBuilder(Transformer):
    def __init__(self, spec_slots: Dict[str, RawSpec]) -> None:
        super().__init__()
        self.spec_slots = spec_slots

    def start(self, program):
        return program

    def program(self, *children):
        if not children:
            return Program(func_decls=[], stmt_seq=[Pass()])
        *funcs, stmt_seq = children
        return Program(func_decls=list(funcs), stmt_seq=stmt_seq or [Pass()])

    def stmt_seq(self, *stmts):
        return list(stmts) or []

    def spec_annot(self, slot: Token):
        return SpecAnnot(self.spec_slots[str(slot)])

    # statements
    def decl_stmt(self, name, type_node): return Decl(str(name), type_node)
    def assign_stmt(self, name, expr): return Assign(str(name), expr)
    def list_assign_stmt(self, target, expr): return ListAssign(target, expr)
    def pass_stmt(self): return Pass()
    def empty_stmt(self): return Pass()
    def while_stmt(self, cond, body_seq): return While(cond=cond, body=body_seq or [Pass()])

    def if_stmt(self, cond, then_seq): return If(cond=cond, then_s=then_seq or [Pass()], else_s=None)
    def if_else_stmt(self, cond, then_seq, else_seq): return If(cond=cond, then_s=then_seq or [Pass()], else_s=else_seq or [Pass()])

    # types
    def type_int(self): return TypeInt()
    def type_float(self): return TypeFloat()
    def type_list(self, elem, dimension, shape): return TypeList(elem, IntLit(int(dimension)), shape)
    def dynamic_list_type(self, elem, dimension): return TypeDynamicList(elem, IntLit(int(dimension)))

    def aexp_list(self, *items):
        return list(items)

    # function parameters
    def param_type(self, name, type_node):
        return Param(str(name), type_node)

    def param_list(self, *params):
        return list(params)

    # imm/aexp
    def aexp(self, expr): return expr
    def int_lit(self, tok): return IntLit(int(tok))
    def float_lit(self, tok): return FloatLit(float(tok))
    def list_lit(self, *args):
        return ListLit(list(args))
    def a_var(self, name): return AVar(str(name))
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

    # bexp
    def bexp(self, expr): return expr
    def btrue(self): return BBool(True)
    def bfalse(self): return BBool(False)
    def bnot(self, rhs): return BNot(rhs)
    def band(self, l, r): return BBinOp("and", l, r)
    def bxor(self, l, r): return BBinOp("xor", l, r)
    def bor(self, l, r): return BBinOp("or", l, r)
    def compare(self, l, op, r): return BCompare(str(op), l, r)
    def truthy(self, aexp): return BTruthy(aexp)

    # functions
    def func_decl(self, name, *rest):
        if len(rest) == 2:
            params = None
            ret_type, body = rest
        elif len(rest) == 3:
            params, ret_type, body = rest
        else:
            raise ParserError("Invalid function declaration.")

        if params is None:
            normalized_params = []
        elif isinstance(params, Param): # single parameter case
            normalized_params = [params]
        else:
            normalized_params = list(params)
        return FunctionDecl(str(name), normalized_params, ret_type, body or [])

    def func_stmt_seq(self, *stmts):
        return list(stmts) or []

    def func_if_stmt(self, cond, then_seq, else_seq=None):
        if else_seq is None:
            return If(cond=cond, then_s=then_seq or [Pass()], else_s=None)
        return If(cond=cond, then_s=then_seq or [Pass()], else_s=else_seq or [Pass()])

    def func_if_else_stmt(self, cond, then_seq, else_seq):
        return If(cond=cond, then_s=then_seq or [Pass()], else_s=else_seq or [Pass()])

    def func_while_stmt(self, cond, body_seq):
        return While(cond=cond, body=body_seq or [Pass()])

    def return_stmt(self, expr):
        return Return(expr)

    def func_call(self, name, args):
        return FuncCall(str(name), args or [])

    def arg_list_opt(self, args=None):
        return args or []

    def arg_list(self, *args):
        return list(args)


SELVERI_PARSER = Lark.open(
    str(Path(__file__).resolve().parent / "grammars" / "grammar.lark"),
    parser="lalr",
    lexer="contextual",
    maybe_placeholders=False,
)

def parse_selveri(src: str) -> Program:
    """Parse SelVeri source code into an executable AST with raw spec payloads."""
    rewritten_src, raw_specs = extract_raw_specs(src) # preprocess the source code to extract raw specs
    try:
        tree = SELVERI_PARSER.parse(rewritten_src)
    except LarkError as e:
        raise ParserError("Failed to parse SelVeri source code. " + str(e)) from None
    return AstBuilder(raw_specs).transform(tree)


def _iter_stmt_specs(stmts: List[Stmt]) -> Iterator[RawSpec]:
    for stmt in stmts:
        if isinstance(stmt, SpecAnnot):
            yield stmt.spec
            continue
        if isinstance(stmt, If):
            yield from _iter_stmt_specs(stmt.then_s)
            if stmt.else_s is not None:
                yield from _iter_stmt_specs(stmt.else_s)
            continue
        if isinstance(stmt, While):
            yield from _iter_stmt_specs(stmt.body)


def collect_raw_specs(program: Program) -> List[RawSpec]:
    specs: List[RawSpec] = []
    for func in program.func_decls:
        specs.extend(_iter_stmt_specs(func.body))
    specs.extend(_iter_stmt_specs(program.stmt_seq))
    return specs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse SelVeri source file to AST")
    parser.add_argument("input_file", type=Path, help="Path to the .sv source file")
    args = parser.parse_args()
    with open(args.input_file, "r", encoding="utf-8") as f:
        src = f.read()
    ast = parse_selveri(src)
    print(ast)
