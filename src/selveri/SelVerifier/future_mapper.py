from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from ..errors import VerificationError
from ..specs import Spec, SpecBinOp, SpecType, SpecUnOp

@dataclass(frozen=True)
class MappedFutureFormula:
    formula_text: str
    atom_table: Dict[str, Spec]

class FutureLTLMapper:
    def __init__(self) -> None:
        self.atom_names: Dict[Spec, str] = {}
        self.atom_table: Dict[str, Spec] = {}
        self.next_atom_index = 0

    def map(self, spec: Spec) -> MappedFutureFormula:
        formula_text = self.map_spec(spec)
        return MappedFutureFormula(formula_text=formula_text, atom_table=dict(self.atom_table))

    def map_spec(self, spec: Spec) -> str:
        if spec.type == SpecType.FOL:
            return self.intern_atom(spec)

        if not isinstance(spec, (SpecUnOp, SpecBinOp)):
            raise VerificationError(
                f"Unsupported specification node for future LTL mapping: {type(spec).__name__}"
            )

        if isinstance(spec, SpecUnOp):
            if spec.op == "!":
                return f"!({self.map_spec(spec.rhs)})"
            if spec.op == "Next":
                return f"X({self.map_spec(spec.rhs)})"
            if spec.op == "Eventually":
                return f"F({self.map_spec(spec.rhs)})"
            if spec.op == "Always":
                return f"G({self.map_spec(spec.rhs)})"
            if spec.op in {"Previously", "Once", "Historically"}:
                raise VerificationError(
                    f"Past LTL operator '{spec.op}' is not supported in future-LTL verification."
                )
            raise VerificationError(f"Unsupported unary operator in future LTL mapping: {spec.op}")

        if spec.op == "&&":
            return f"({self.map_spec(spec.left)}) & ({self.map_spec(spec.right)})"
        if spec.op == "||":
            return f"({self.map_spec(spec.left)}) | ({self.map_spec(spec.right)})"
        if spec.op == "=>":
            return f"({self.map_spec(spec.left)}) -> ({self.map_spec(spec.right)})"
        if spec.op == "Until":
            return f"({self.map_spec(spec.left)}) U ({self.map_spec(spec.right)})"
        if spec.op == "Since":
            raise VerificationError(
                "Past LTL operator 'Since' is not supported in future-LTL verification."
            )
        raise VerificationError(f"Unsupported binary operator in future LTL mapping: {spec.op}")

    def intern_atom(self, spec: Spec) -> str:
        if spec in self.atom_names:
            return self.atom_names[spec]

        atom_name = f"p{self.next_atom_index}"
        self.next_atom_index += 1
        self.atom_names[spec] = atom_name
        self.atom_table[atom_name] = spec
        return atom_name
