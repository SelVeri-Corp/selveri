from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..runtime import DeclType



@dataclass(frozen=True)
class RuntimeConfiguration:
    state: Dict[str, Any]
    scope: Dict[str, Optional[DeclType]]
    
@dataclass(frozen=True)
class Formula:
    uid: int

@dataclass
class TemporalObligation:
    kind: str
    formula: Any
    aux_formula: Any | None
    created_at_step: int
    source_spec: str