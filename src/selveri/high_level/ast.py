"""High-level SelVeri AST node definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from selveri.common.diagnostics import SourceSpan
from selveri.common.errors import ParserError
from selveri.common.types import real
from selveri.spec.models import RawSpec


class AExp:
    pass


class BExp:
    pass


class Stmt:
    pass


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
    dimension: IntLit
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
    dimension: IntLit
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
    func_decls: List[FunctionDecl]
    stmt_seq: List[Stmt]


@dataclass(frozen=True)
class FunctionDecl(Spanned):
    name: str
    params: List[Param]
    return_type: TypeNode
    body: List[Stmt]
