from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


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
    name: str

@dataclass(frozen=True)
class DomainType(Domain):
    type_node: object

@dataclass(frozen=True)
class DomainSet(Domain):
    items: List[object]

@dataclass(frozen=True)
class DomainInterval(Domain):
    lo: object
    hi: object
    right_open: bool

    def __str__(self) -> str:
        return f"[{self.lo}, {self.hi})" if self.right_open else f"[{self.lo}, {self.hi}]"

@dataclass(frozen=True)
class Spec:
    uid: int

    def __hash__(self) -> int:
        return hash(self.uid) # TODO: hashing uid itself is unnecessary, as uids are already unique

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
