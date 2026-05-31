from __future__ import annotations

from dataclasses import dataclass

from selveri.common.runtime import Scope, State


@dataclass(frozen=True)
class RuntimeConfiguration:
    state: State
    scope: Scope
