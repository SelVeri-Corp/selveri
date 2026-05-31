from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple, Union

real = type(0.0)


@dataclass
class IRInstr:
    label: int
    op: str
    args: Tuple[Union[str, int, real], ...] = ()
    span: object | None = None

    def render(self) -> str:
        rendered_args = ", ".join(self._render_arg(index, arg) for index, arg in enumerate(self.args))
        return f"{self.label}: {self.op}" + (f" {rendered_args}" if rendered_args else "")

    def _render_arg(self, index: int, arg: Union[str, int, real]) -> str:
        if self.op == "VERI" and isinstance(arg, str):
            return repr(arg)
        if self.op == "VERIP" and index == 1 and isinstance(arg, str):
            return repr(arg)
        if self.op == "OBT" and index == 2 and isinstance(arg, str):
            return repr(arg)
        return str(arg)
