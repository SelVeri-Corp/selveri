from .config import RuntimeConfiguration
from .interpreter import ExecutionResult, interpret_ir_code, interpret_ir_text

__all__ = [
    "ExecutionResult",
    "RuntimeConfiguration",
    "interpret_ir_code",
    "interpret_ir_text",
]
