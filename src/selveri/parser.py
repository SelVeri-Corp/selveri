from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from lark import Lark, LarkError, Token, Transformer, UnexpectedInput, v_args
from lark.exceptions import VisitError

from .defs import AExp, BExp, Stmt
from .diagnostics import (
    DiagnosticCode,
    DiagnosticLabel,
    SourceFile,
    SourceSpan,
    format_found_token,
    render_expected_tokens,
)
from .errors import ParserError, parse_error
from .preprocessor import extract_raw_specs
from .specs import RawSpec

real = type(0.0)


# =========================
# AST
# =========================
@dataclass(frozen=True)
class Spanned:
    span: SourceSpan | None = field(default=None, kw_only=True)


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
class TypeInt(Spanned, BasicType):
    def __str__(self) -> str:
        return "INT"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, TypeInt)

    def __hash__(self) -> int:
        return hash(TypeInt)

@dataclass(frozen=True)
class TypeReal(Spanned, BasicType):
    def __str__(self) -> str:
        return "REAL"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, TypeReal)

    def __hash__(self) -> int:
        return hash(TypeReal)

@dataclass(frozen=True)
class TypeList(Spanned, ConcreteType):
    elem: BasicType
    dimension: "IntLit"
    shape: List[AExp] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.dimension.value != len(self.shape):
            raise ParserError(
                f"List type dimension ({self.dimension.value}) does not match "
                f"declared shape count ({len(self.shape)}).",
                span=self.span,
            )

    def __str__(self) -> str:
        return f"LIST[{self.elem},{self.dimension}{f', {self.shape}' if self.shape else ''}]"

@dataclass(frozen=True)
class TypeDynamicList(Spanned, TypeNode):
    elem: BasicType
    dimension: "IntLit"
    shape: List[Optional[AExp]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if len(self.shape) > self.dimension.value:
            raise ParserError(
                f"Dynamic list type dimension ({self.dimension.value}) cannot be smaller "
                f"than declared shape count ({len(self.shape)}).",
                span=self.span,
            )

    def __str__(self) -> str:
        return f"LIST[{self.elem}, {self.dimension}{f', {self.shape}' if self.shape else ''}]"

# Immediates / Arithmetic
@dataclass(frozen=True)
class IntLit(Spanned, Imm, AExp):
    value: int

    def __str__(self) -> str:
        return str(self.value)

@dataclass(frozen=True)
class RealLit(Spanned, Imm, AExp):
    value: real

    def __str__(self) -> str:
        return str(self.value)

@dataclass(frozen=True)
class ListLit(Spanned, Imm, AExp):
    items: List[Imm]


@dataclass(frozen=True)
class AVar(Spanned, AExp):
    name: str

    def __str__(self) -> str:
        return self.name

@dataclass(frozen=True)
class ALen(Spanned, AExp):
    name: str

    def __str__(self) -> str:
        return f"len({self.name})"

@dataclass(frozen=True)
class AIndex(Spanned, AExp):
    base: AExp
    index: AExp

    def __str__(self) -> str:
        return f"{self.base}[{self.index}]"

@dataclass(frozen=True)
class ARead(Spanned, AExp):
    def __str__(self) -> str:
        return "read()"

@dataclass(frozen=True)
class AUnOp(Spanned, AExp):
    op: str
    rhs: AExp

    def __str__(self) -> str:
        return f"{self.op}{self.rhs}"

@dataclass(frozen=True)
class ABinOp(Spanned, AExp):
    op: str
    left: AExp
    right: AExp

    def __str__(self) -> str:
        return f"{self.left} {self.op} {self.right}"

# Boolean
@dataclass(frozen=True)
class BBool(Spanned, BExp):
    value: bool

    def __str__(self) -> str:
        return "true" if self.value else "false"


@dataclass(frozen=True)
class BNot(Spanned, BExp):
    rhs: BExp

    def __str__(self) -> str:
        return f"!{self.rhs}"


@dataclass(frozen=True)
class BBinOp(Spanned, BExp):
    op: str
    left: BExp
    right: BExp

    def __str__(self) -> str:
        return f"{self.left} {self.op} {self.right}"


@dataclass(frozen=True)
class BCompare(Spanned, BExp):
    op: str
    left: AExp
    right: AExp

    def __str__(self) -> str:
        return f"{self.left} {self.op} {self.right}"


@dataclass(frozen=True)
class BTruthy(Spanned, BExp):
    aexp: AExp


@dataclass(frozen=True)
class BSpec(Spanned, BExp):
    spec: RawSpec


@dataclass(frozen=True)
class Decl(Spanned, Stmt):
    name: str
    type_node: TypeNode


@dataclass(frozen=True)
class Assign(Spanned, Stmt):
    name: str
    aexp: AExp


@dataclass(frozen=True)
class ListAssign(Spanned, Stmt):
    target: AIndex
    aexp: AExp


@dataclass(frozen=True)
class Pass(Spanned, Stmt):
    pass


@dataclass(frozen=True)
class AObtain(Spanned, AExp):
    var_name: str
    spec: RawSpec


@dataclass(frozen=True)
class SpecAnnot(Spanned, Stmt):
    spec: RawSpec


@dataclass(frozen=True)
class If(Spanned, Stmt):
    cond: BExp
    then_s: List[Stmt]
    else_s: Optional[List[Stmt]]


@dataclass(frozen=True)
class While(Spanned, Stmt):
    cond: BExp
    body: List[Stmt]


@dataclass(frozen=True)
class Write(Spanned, Stmt):
    aexp: AExp


@dataclass(frozen=True)
class WriteLine(Spanned, Stmt):
    aexp: AExp


@dataclass(frozen=True)
class Param(Spanned):
    name: str
    type_node: TypeNode


@dataclass(frozen=True)
class Return(Spanned, Stmt):
    value: AExp


@dataclass(frozen=True)
class FuncCall(Spanned, AExp, Stmt):
    name: str
    args: List[AExp]


@dataclass(frozen=True)
class Program(Spanned):
    func_decls: List["FunctionDecl"]
    stmt_seq: List["Stmt"]


@dataclass(frozen=True)
class FunctionDecl(Spanned):
    name: str
    params: List[Param]
    return_type: TypeNode
    body: List[Stmt]


@v_args(meta=True, inline=True)
class AstBuilder(Transformer):
    def __init__(self, spec_slots: Dict[str, RawSpec], source_file: SourceFile) -> None:
        super().__init__()
        self.spec_slots = spec_slots
        self.source_file = source_file

    def _span(self, meta: object) -> SourceSpan:
        return SourceSpan.from_lark_meta(self.source_file, meta)

    def _token_span(self, token: Token) -> SourceSpan:
        return SourceSpan.from_token(self.source_file, token)

    def _pass(self, meta: object) -> Pass:
        return Pass(span=self._span(meta))

    def start(self, _meta, program):
        return program

    def program(self, meta, *children):
        if not children:
            return Program(func_decls=[], stmt_seq=[self._pass(meta)], span=self._span(meta))
        *funcs, stmt_seq = children
        return Program(func_decls=list(funcs), stmt_seq=stmt_seq or [self._pass(meta)], span=self._span(meta))

    def stmt_seq(self, _meta, *stmts):
        return list(stmts) or []

    def spec_annot(self, meta, slot: Token):
        spec = self.spec_slots[str(slot)]
        return SpecAnnot(spec, span=spec.location or self._span(meta))

    # statements
    def decl_stmt(self, meta, name, type_node): return Decl(str(name), type_node, span=self._span(meta))
    def assign_stmt(self, meta, name, expr): return Assign(str(name), expr, span=self._span(meta))
    def list_assign_stmt(self, meta, target, expr): return ListAssign(target, expr, span=self._span(meta))
    def write_stmt(self, meta, aexp): return Write(aexp, span=self._span(meta))
    def writeline_stmt(self, meta, aexp): return WriteLine(aexp, span=self._span(meta))
    def pass_stmt(self, meta): return self._pass(meta)
    def empty_stmt(self, meta): return self._pass(meta)
    def while_stmt(self, meta, cond, body_seq): return While(cond=cond, body=body_seq or [self._pass(meta)], span=self._span(meta))

    def bspec(self, meta, slot: Token):
        spec = self.spec_slots[str(slot)]
        return BSpec(spec, span=spec.location or self._span(meta))

    def a_obtain(self, meta, name: Token, slot: Token):
        return AObtain(str(name), self.spec_slots[str(slot)], span=self._span(meta))

    def if_stmt(self, meta, cond, then_seq):
        return If(cond=cond, then_s=then_seq or [self._pass(meta)], else_s=None, span=self._span(meta))

    def if_else_stmt(self, meta, cond, then_seq, else_seq):
        return If(cond=cond, then_s=then_seq or [self._pass(meta)], else_s=else_seq or [self._pass(meta)], span=self._span(meta))

    # types
    def type_int(self, meta): return TypeInt(span=self._span(meta))
    def type_real(self, meta): return TypeReal(span=self._span(meta))
    def type_list(self, meta, elem, dimension, shape):
        return TypeList(elem, IntLit(int(dimension), span=self._token_span(dimension)), shape, span=self._span(meta))
    def dynamic_list_type(self, meta, elem, dimension):
        return TypeDynamicList(elem, IntLit(int(dimension), span=self._token_span(dimension)), span=self._span(meta))

    def aexp_list(self, _meta, *items): return list(items)
    # function parameters
    def param_type(self, meta, name, type_node): return Param(str(name), type_node, span=self._span(meta))
    def param_list(self, _meta, *params): return list(params)
    # imm/aexp
    def aexp(self, _meta, expr): return expr
    def int_lit(self, _meta, tok): return IntLit(int(tok), span=self._token_span(tok))
    def real_lit(self, _meta, tok): return RealLit(real(tok), span=self._token_span(tok))
    def list_lit(self, meta, *args): return ListLit(list(args), span=self._span(meta))
    def a_var(self, _meta, name): return AVar(str(name), span=self._token_span(name))
    def a_len(self, meta, name): return ALen(str(name), span=self._span(meta))
    def _mk_a_index(self, meta, base, idx):
        if not isinstance(base, AExp):
            base = AVar(str(base), span=self._token_span(base))
        return AIndex(base, idx, span=self._span(meta))
    def a_index_base(self, meta, base, idx):
        return self._mk_a_index(meta, base, idx)
    def a_index_chain(self, meta, base, idx):
        return self._mk_a_index(meta, base, idx)
    def a_read(self, meta): return ARead(span=self._span(meta))
    def neg(self, meta, rhs): return AUnOp("-", rhs, span=self._span(meta))
    def add(self, meta, l, r): return ABinOp("+", l, r, span=self._span(meta))
    def sub(self, meta, l, r): return ABinOp("-", l, r, span=self._span(meta))
    def mul(self, meta, l, r): return ABinOp("*", l, r, span=self._span(meta))
    def div(self, meta, l, r): return ABinOp("/", l, r, span=self._span(meta))

    # bexp
    def bexp(self, _meta, expr): return expr
    def btrue(self, meta): return BBool(True, span=self._span(meta))
    def bfalse(self, meta): return BBool(False, span=self._span(meta))
    def bnot(self, meta, rhs): return BNot(rhs, span=self._span(meta))
    def band(self, meta, l, r): return BBinOp("and", l, r, span=self._span(meta))
    def bxor(self, meta, l, r): return BBinOp("xor", l, r, span=self._span(meta))
    def bor(self, meta, l, r): return BBinOp("or", l, r, span=self._span(meta))
    def compare(self, meta, l, op, r): return BCompare(str(op), l, r, span=self._span(meta))
    def truthy(self, meta, aexp): return BTruthy(aexp, span=self._span(meta))

    # functions
    def func_decl(self, meta, name, *rest):
        if len(rest) == 2:
            params = None
            ret_type, body = rest
        elif len(rest) == 3:
            params, ret_type, body = rest
        else:
            raise ParserError("Invalid function declaration.", span=self._span(meta))

        if params is None:
            normalized_params = []
        elif isinstance(params, Param): # single parameter case
            normalized_params = [params]
        else:
            normalized_params = list(params)
        return FunctionDecl(str(name), normalized_params, ret_type, body or [], span=self._span(meta))

    def func_stmt_seq(self, _meta, *stmts): return list(stmts) or []

    def func_if_stmt(self, meta, cond, then_seq, else_seq=None):
        if else_seq is None:
            return If(cond=cond, then_s=then_seq or [self._pass(meta)], else_s=None, span=self._span(meta))
        return If(cond=cond, then_s=then_seq or [self._pass(meta)], else_s=else_seq or [self._pass(meta)], span=self._span(meta))

    def func_if_else_stmt(self, meta, cond, then_seq, else_seq):
        return If(cond=cond, then_s=then_seq or [self._pass(meta)], else_s=else_seq or [self._pass(meta)], span=self._span(meta))

    def func_while_stmt(self, meta, cond, body_seq):
        return While(cond=cond, body=body_seq or [self._pass(meta)], span=self._span(meta))

    def return_stmt(self, meta, expr): return Return(expr, span=self._span(meta))
    def func_call(self, meta, name, args=None):
        # With inlined arg_list (grammar.lark), one argument is passed as a bare aexp;
        # zero arguments go through arg_list_opt -> []; two or more use arg_list -> list.
        if args is None:
            normalized: List[AExp] = []
        elif isinstance(args, list):
            normalized = args
        else:
            normalized = [args]
        return FuncCall(str(name), normalized, span=self._span(meta))

    def arg_list_opt(self, _meta, args=None):
        if args is None:
            return []
        return args if isinstance(args, list) else [args]

    def arg_list(self, _meta, *args):
        return list(args)


SELVERI_PARSER = Lark.open(
    str(Path(__file__).resolve().parent / "grammars" / "grammar.lark"),
    parser="lalr",
    lexer="contextual",
    maybe_placeholders=False,
    propagate_positions=True,
)


def _parser_error_from_lark(exc: LarkError, source_file: SourceFile) -> ParserError:
    if isinstance(exc, UnexpectedInput):
        token = getattr(exc, "token", None)
        if token is not None:
            span = SourceSpan.from_token(source_file, token)
        else:
            pos = getattr(exc, "pos_in_stream", 0) or 0
            span = SourceSpan(
                source=source_file,
                start_line=getattr(exc, "line", 1),
                start_column=getattr(exc, "column", 1),
                end_line=getattr(exc, "line", 1),
                end_column=getattr(exc, "column", 1) + 1,
                start_pos=pos,
                end_pos=pos + 1,
            )
        expected = set(getattr(exc, "expected", ()) or ())
        found = format_found_token(token)
        label = f"found {found}"
        title = "unexpected token"
        code = DiagnosticCode.PARSE_UNEXPECTED_TOKEN
        hint = None
        message = render_expected_tokens(expected)
        if token is None or getattr(token, "type", None) == "$END":
            title = "unexpected end of input"
            label = "input ended here"
            if "FI" in expected:
                title = "missing block terminator"
                code = DiagnosticCode.PARSE_MISSING_BLOCK_TERMINATOR
                message = "expected `fi` before end of input"
                hint = "close the conditional block with `fi`"
                for line_no in range(span.start_line, 0, -1):
                    text = source_file.get_line(line_no)
                    column = len(text) - len(text.lstrip()) + 1
                    if text.lstrip().startswith("if "):
                        start = SourceSpan(source_file, line_no, column, line_no, column + 2, 0, 0)
                        span = start
                        label = "`if` block starts here"
                        break
            elif "OD" in expected:
                title = "missing block terminator"
                code = DiagnosticCode.PARSE_MISSING_BLOCK_TERMINATOR
                message = "expected `od` before end of input"
                hint = "close the loop block with `od`"
                for line_no in range(span.start_line, 0, -1):
                    text = source_file.get_line(line_no)
                    column = len(text) - len(text.lstrip()) + 1
                    if text.lstrip().startswith("while "):
                        start = SourceSpan(source_file, line_no, column, line_no, column + 5, 0, 0)
                        span = start
                        label = "`while` block starts here"
                        break
        elif getattr(token, "value", None) == ";" and {"IDENT", "INT_LIT", "REAL_LIT", "LPAR", "LEN"} & expected:
            hint = "write an expression after `:=`"
        return parse_error(
            message,
            span=span,
            title=title,
            code=code,
            labels=(DiagnosticLabel(span, label, "primary"),),
            hint=hint,
        )
    return parse_error(f"Failed to parse SelVeri source code. {exc}")


def parse_selveri(src: str, source_path: str | Path | None = None) -> Program:
    source_file = SourceFile(str(source_path or ""), src)
    rewritten_src, raw_specs = extract_raw_specs(src, source_file)
    try:
        tree = SELVERI_PARSER.parse(rewritten_src)
    except LarkError as exc:
        raise _parser_error_from_lark(exc, source_file) from None
    try:
        return AstBuilder(raw_specs, source_file).transform(tree)
    except VisitError as exc:
        if isinstance(exc.orig_exc, ParserError):
            raise exc.orig_exc from None
        raise


def _iter_aexp_specs(aexp: AExp) -> Iterator[RawSpec]:
    """Yield any RawSpec embedded inside arithmetic expressions (AObtain nodes)."""
    if isinstance(aexp, AObtain):
        yield aexp.spec
    elif isinstance(aexp, AUnOp):
        yield from _iter_aexp_specs(aexp.rhs)
    elif isinstance(aexp, ABinOp):
        yield from _iter_aexp_specs(aexp.left)
        yield from _iter_aexp_specs(aexp.right)
    elif isinstance(aexp, AIndex):
        yield from _iter_aexp_specs(aexp.base)
        yield from _iter_aexp_specs(aexp.index)


def _iter_bexp_specs(bexp: BExp) -> Iterator[RawSpec]:
    """Yield any RawSpec embedded inside boolean expressions (BSpec nodes)."""
    if isinstance(bexp, BSpec):
        yield bexp.spec
    elif isinstance(bexp, BNot):
        yield from _iter_bexp_specs(bexp.rhs)
    elif isinstance(bexp, BBinOp):
        yield from _iter_bexp_specs(bexp.left)
        yield from _iter_bexp_specs(bexp.right)
    elif isinstance(bexp, BCompare):
        yield from _iter_aexp_specs(bexp.left)
        yield from _iter_aexp_specs(bexp.right)
    elif isinstance(bexp, BTruthy):
        yield from _iter_aexp_specs(bexp.aexp)

def _iter_stmt_specs(stmts: List[Stmt]) -> Iterator[RawSpec]:
    for stmt in stmts:
        if isinstance(stmt, SpecAnnot):
            yield stmt.spec
            continue
        if isinstance(stmt, Assign):
            yield from _iter_aexp_specs(stmt.aexp)
            continue
        if isinstance(stmt, (Write, WriteLine)):
            yield from _iter_aexp_specs(stmt.aexp)
            continue
        if isinstance(stmt, If):
            yield from _iter_bexp_specs(stmt.cond)
            yield from _iter_stmt_specs(stmt.then_s)
            if stmt.else_s is not None:
                yield from _iter_stmt_specs(stmt.else_s)
            continue
        if isinstance(stmt, While):
            yield from _iter_bexp_specs(stmt.cond)
            yield from _iter_stmt_specs(stmt.body)
            continue
        if isinstance(stmt, Return):
            yield from _iter_aexp_specs(stmt.value)
            continue


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
    ast = parse_selveri(src, args.input_file)
    print(ast)
