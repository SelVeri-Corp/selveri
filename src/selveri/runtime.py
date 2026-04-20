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
class State:
    values: Dict[str, Any]
    parent: Optional["State"] = None

    def __contains__(self, name: str) -> bool:
        return self.find_owner(name) is not None

    def __getitem__(self, name: str) -> Any:
        owner = self.find_owner(name)
        if owner is None:
            raise KeyError(name)
        return owner.values[name]

    def __setitem__(self, name: str, value: Any) -> None:
        self.values[name] = value

    def get(self, name: str, default: Any = None) -> Any:
        owner = self.find_owner(name)
        if owner is None:
            return default
        return owner.values[name]

    def items(self):
        return self.visible_values().items()

    def find_owner(self, name: str) -> Optional["State"]:
        cur: Optional[State] = self
        while cur is not None:
            if name in cur.values:
                return cur
            cur = cur.parent
        return None

    def visible_values(self) -> Dict[str, Any]:
        visible: Dict[str, Any] = {}
        cur: Optional[State] = self
        while cur is not None:
            for name, value in cur.values.items():
                if value is not _UNSET and name not in visible:
                    visible[name] = value
            cur = cur.parent
        return visible


@dataclass
class Scope:
    types: Dict[str, Optional[DeclType]]
    parent: Optional["Scope"] = None

    def __contains__(self, name: str) -> bool:
        return self.find_owner(name) is not None

    def __getitem__(self, name: str) -> Optional[DeclType]:
        owner = self.find_owner(name)
        if owner is None:
            raise KeyError(name)
        return owner.types[name]

    def __setitem__(self, name: str, decl_type: Optional[DeclType]) -> None:
        self.types[name] = decl_type

    def get(self, name: str, default: Optional[DeclType] = None) -> Optional[DeclType]:
        owner = self.find_owner(name)
        if owner is None:
            return default
        return owner.types[name]

    def items(self):
        return self.visible_types().items()

    def find_owner(self, name: str) -> Optional["Scope"]:
        cur: Optional[Scope] = self
        while cur is not None:
            if name in cur.types:
                return cur
            cur = cur.parent
        return None

    def visible_types(self) -> Dict[str, DeclType]:
        visible: Dict[str, DeclType] = {}
        cur: Optional[Scope] = self
        while cur is not None:
            for name, decl_type in cur.types.items():
                if decl_type is not None and name not in visible:
                    visible[name] = decl_type
            cur = cur.parent
        return visible
