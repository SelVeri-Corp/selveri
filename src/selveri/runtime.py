"""Runtime values shared by the interpreter and verifier (avoids circular imports)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class DeclType:
    kind: str  # "INT" | "FLOAT" | "LIST"
    elem_kind: Optional[str]  # for LIST: "INT" | "FLOAT"
    size: Optional[int]  # for LIST: fixed size for DECL, None for LDECL


_UNSET = object()


@dataclass
class RuntimeScope:
    values: Dict[str, Any]
    types: Dict[str, Optional[DeclType]]
