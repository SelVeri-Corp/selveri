from types import SimpleNamespace
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional
from z3 import *
from ltlf2dfa.parser.ltlf import LTLfParser

from .defs import RuntimeConfiguration, TemporalObligation
from ..compiler import IRInstr
from ..errors import ParserError, VerificationError
from ..spec_parser import parse_spec
from ..specs import ParsedSpec, RawSpec, Spec
from .mapper import Z3Mapper

class VerificationEngine():
    def __init__(self):
        self.last_step = 0
        self.history: list[RuntimeConfiguration] = []
        self.pending: list[TemporalObligation] = []
        self.specs_by_id: Dict[int, ParsedSpec] = {}
        self.prepared = False

        self.solver : Solver = Solver()
        self.mapper: Z3Mapper = None

    # the verifier owns spec parsing and caches parsed ASTs before execution starts.
    def prepare_program(
        self,
        program: Iterable[IRInstr],
        *,
        raw_specs: Optional[Iterable[RawSpec]] = None,
    ) -> None:
        collected_specs = list(raw_specs) if raw_specs is not None else self.collect_raw_specs(program) # collect raw specs from the program
        
        self.specs_by_id.clear()
        for raw_spec in collected_specs: # parse and register the raw specs
            parsed_spec = self.parse_raw_spec(raw_spec)
            spec_id = parsed_spec.raw_spec.spec_id
            if spec_id in self.specs_by_id:
                raise VerificationError(f"Duplicate specification id: {spec_id}")
            self.specs_by_id[spec_id] = parsed_spec

        self.prepared = True # the program is prepared

    def collect_raw_specs(self, program: Iterable[IRInstr]) -> List[RawSpec]:
        raw_specs: List[RawSpec] = []
        for instr in program:
            if instr.op != "VERI":
                continue
            if len(instr.args) < 2:
                raise VerificationError("Malformed VERI instruction encountered during verifier preparation.")
            raw_specs.append(
                RawSpec(
                    spec_id=int(instr.args[0]),
                    text=str(instr.args[1]),
                )
            )
        return raw_specs

    def parse_raw_spec(self, raw_spec: RawSpec) -> ParsedSpec:
        try:
            spec_ast = parse_spec(raw_spec.text)
        except ParserError as exc:
            if raw_spec.location is None:
                raise VerificationError(
                    f"Failed to parse specification #{raw_spec.spec_id}: {exc}"
                ) from None
            raise VerificationError(
                "Failed to parse specification "
                f"#{raw_spec.spec_id} at "
                f"{raw_spec.location.start.line}:{raw_spec.location.start.column}: {exc}"
            ) from None
        return ParsedSpec(raw_spec=raw_spec, ast=spec_ast)

    def resolve_spec(self, spec_id: int) -> ParsedSpec: # resolve a spec by its id
        if not self.prepared:
            raise VerificationError("Specifications must be prepared before execution starts.")
        if spec_id not in self.specs_by_id:
            raise VerificationError(f"Unknown specification id: {spec_id}")
        return self.specs_by_id[spec_id]

    def handle_veri(self, spec_id: int, snapshot: RuntimeConfiguration) -> ParsedSpec: # handle a veri instruction
        parsed_spec = self.resolve_spec(spec_id) # resolve the spec by its id
        self.on_veri(parsed_spec.ast, snapshot) # call the on_veri callback
        return parsed_spec

    def on_program_start(self) -> None: ...
    
    def on_step(self, snapshot: RuntimeConfiguration) -> None:
        # TODO: investigate the memory management here
        self.history[self.last_step] = snapshot
        self.last_step += 1
    
    
    def on_veri(self, spec: Spec, snapshot: RuntimeConfiguration) -> None: ...

    # TODO: consider optimizations: updating the mapper at each IR assignment and declaration, then use push/pop instead of reset
    def verify(self, spec: Spec, snapshot: RuntimeConfiguration) -> bool: 
        self.solver.reset()
        self.mapper = Z3Mapper(snapshot, self.solver)

        # TODO: check spec type here (FOL or LTL)
        return self.verify_FOL(spec)
        


    def verify_FOL(self, spec: Spec) -> bool:
        negated_assumption = simplify(Not(self.mapper.map_FOL(spec)))
        result = self.solver.check(negated_assumption)
        if result == unsat:
            return True
        else: # sat
            return False

    def verify_past_LTL(self, spec: Spec, snapshot: RuntimeConfiguration) -> bool: ...
    def verify_future_LTL(self, spec: Spec, snapshot: RuntimeConfiguration) -> bool: ...
    def on_program_end(self) -> None: ...
