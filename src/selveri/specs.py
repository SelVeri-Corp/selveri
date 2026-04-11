from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional


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

class Spec:
    pass

@dataclass(frozen=True)
class SpecFromBExp(Spec):
    bexp: object

    def __str__(self) -> str:
        return str(self.bexp)

@dataclass(frozen=True)
class SpecUnOp(Spec):
    op: str
    rhs: Spec

@dataclass(frozen=True)
class SpecBinOp(Spec):
    op: str
    left: Spec
    right: Spec

    def __str__(self) -> str:
        return f"{self.left} {self.op} {self.right}"

@dataclass(frozen=True)
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

@dataclass
class SpecRegistry:
    _entries: Dict[int, ParsedSpec] = field(default_factory=dict)
    _order: List[int] = field(default_factory=list)

    def register(self, parsed_spec: ParsedSpec) -> None:
        spec_id = parsed_spec.raw_spec.spec_id
        if spec_id in self._entries:
            raise ValueError(f"Duplicate specification id: {spec_id}")
        self._entries[spec_id] = parsed_spec
        self._order.append(spec_id)

    def get(self, spec_id: int) -> ParsedSpec:
        return self._entries[spec_id]

    def values(self) -> List[ParsedSpec]:
        return [self._entries[spec_id] for spec_id in self._order]

    def items(self) -> Iterator[tuple[int, ParsedSpec]]:
        for spec_id in self._order:
            yield spec_id, self._entries[spec_id]

    def clear(self) -> None:
        self._entries.clear()
        self._order.clear()

    def __contains__(self, spec_id: int) -> bool:
        return spec_id in self._entries

    def __len__(self) -> int:
        return len(self._entries)
