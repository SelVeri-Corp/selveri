from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

@dataclass(frozen=True)
class RuntimeConfiguration:
    label: int
    op: str
    args: Tuple[Any, ...]
    pc: int
    steps: int
    concrete_stack: List[Any]
    visible_state: Dict[str, Any]
    visible_types: Dict[str, Any]
    scopes: List[Any]          # full _RuntimeScope copies
    call_stack: List[Any]      # full _CallFrame copies

@dataclass(frozen=True)
class Formula:
    uid: int

@dataclass
class TemporalObligation:
    kind: str                 # "NEXT", "EVENTUALLY", "ALWAYS", "UNTIL"
    formula: Any
    aux_formula: Any | None   # for UNTIL, the left side
    created_at_step: int
    source_spec: str

@dataclass
class VerificationErrorInfo:
    pc: int
    label: int
    op: str
    spec_text: str
    message: str
    state: Dict[str, Any]
    steps: int

class VerificationError(Exception):
    def __init__(self, info: VerificationErrorInfo):
        self.info = info
        super().__init__(info.message)

class VerificationEngine():
    def __init__(self, *, keep_history: bool = True):
        self.keep_history = keep_history
        self.history: list[RuntimeConfiguration] = []
        self.pending: list[TemporalObligation] = []
        self.last_pre_state: RuntimeConfiguration | None = None 

    def on_program_start(self) -> None: ...
    def before_instruction(self, snapshot: RuntimeConfiguration) -> None: ...
    def after_instruction(self, before: RuntimeConfiguration, after: RuntimeConfiguration) -> None: ...
    def on_veri(self, spec_text: str, snapshot: RuntimeConfiguration) -> None: ...
    def on_program_end(self) -> None: ...

"""

{ name : spec }
"""