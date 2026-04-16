from __future__ import annotations

from enum import Enum
from dataclasses import dataclass
from typing import List, Optional


class SpecType(Enum):
    FOL = 0
    pLTL = 1
    fLTL = 2


@dataclass(frozen=True)
class SourceLocation:
    line: int
    column: int

@dataclass(frozen=True)
class SourceSpan:
    start: SourceLocation
    end: SourceLocation

@dataclass(frozen=True)
class RawSpec:
    spec_id: int
    text: str
    location: Optional[SourceSpan] = None

class Domain:
    pass

@dataclass(frozen=True)
class DomainIdent(Domain):
    """Domain of values drawn from an identifier."""
    name: str

    def __str__(self) -> str:
        return self.name

@dataclass(frozen=True)
class DomainValues(Domain):
    """Domain of values drawn from a list of literals."""   
    items: List[object]

    def __str__(self) -> str:
        return "[" + ", ".join(str(item) for item in self.items) + "]"

@dataclass(frozen=True)
class DomainRange(Domain):
    """Domain of values drawn from a range of integers."""
    lo: object
    hi: object

    def __str__(self) -> str:
        return f"{self.lo}...{self.hi}"

@dataclass(frozen=True)
class DomainInterval(Domain):
    """Real interval with independent open/closed endpoints (float bounds)."""
    lo: object
    hi: object
    left_closed: bool
    right_closed: bool

    def __str__(self) -> str:
        left = "[" if self.left_closed else "("
        right = "]" if self.right_closed else ")"
        return f"{left}{self.lo}...{self.hi}{right}"

@dataclass(frozen=True)
class DomainType(Domain):
    """Domain given by a scalar type (e.g. Int, Float)."""
    ty: object

    def __str__(self) -> str:
        return str(self.ty)

@dataclass(frozen=True)
class DomainVar(Domain):
    """Domain of values drawn from variables typed as `Var[elem]`."""
    elem: object

    def __str__(self) -> str:
        return f"Var[{self.elem}]"

@dataclass(frozen=True)
class Spec:
    uid: int
    type : SpecType

    def __hash__(self) -> int:
        return self.uid

@dataclass(frozen=True, eq=False)
class SpecFromBExp(Spec):
    bexp: object

    def __str__(self) -> str:
        return str(self.bexp)

@dataclass(frozen=True, eq=False)
class SpecUnOp(Spec):
    op: str
    rhs: Spec

@dataclass(frozen=True, eq=False)
class SpecBinOp(Spec):
    op: str
    left: Spec
    right: Spec

    def __str__(self) -> str:
        return f"{self.left} {self.op} {self.right}"

@dataclass(frozen=True, eq=False)
class SpecQuant(Spec):
    kind: str
    var: str
    domain: Optional[Domain]
    body: Spec

    def __str__(self) -> str:
        if self.domain is None:
            return f"{self.kind} {self.var} . {self.body}"
        return f"{self.kind} {self.var} in {self.domain} . {self.body}"

@dataclass(frozen=True)
class ParsedSpec:
    raw_spec: RawSpec
    ast: Spec
