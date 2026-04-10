from __future__ import annotations

import argparse
import ast
from pathlib import Path
from typing import Any

from .compiler import compile_selveri_source_to_ir_text
from .interpreter import interpret_ir_text


def _parse_initial_state(text: str | None) -> dict[str, Any] | None:
    if text is None:
        return None
    value = ast.literal_eval(text)
    if not isinstance(value, dict):
        raise ValueError("--state must evaluate to a dictionary.")
    return value


def run_pipeline(
    input_path: Path,
    *,
    output_ir: Path | None,
    initial_state: dict[str, Any] | None,
    max_steps: int,
    print_ir: bool,
) -> int:
    with input_path.open("r", encoding="utf-8") as f:
        source = f.read()

    ir_text = compile_selveri_source_to_ir_text(source)

    if output_ir is not None:
        output_ir.parent.mkdir(parents=True, exist_ok=True)
        output_ir.write_text(ir_text, encoding="utf-8")

    if print_ir:
        print("Generated SelVerIR:")
        print(ir_text)
        print()

    result = interpret_ir_text(ir_text, initial_state=initial_state, max_steps=max_steps)

    print("Final state:")
    print(result.state)
    print("\nFinal stack:")
    print(result.stack)
    print(f"\nSteps: {result.steps}")
    return 0


def main() -> int:
    arg_parser = argparse.ArgumentParser(
        description="Run SelVeri parser -> compiler -> interpreter pipeline."
    )
    arg_parser.add_argument("input", type=Path, help="Path to the .svi source file")
    arg_parser.add_argument(
        "-o",
        "--output-ir",
        type=Path,
        default=None,
        help="Optional output path for generated .svir text (disabled by default)",
    )
    arg_parser.add_argument(
        "--emit-ir",
        action="store_true",
        help="Write generated SelVerIR to file (uses --output-ir or input.svir)",
    )
    arg_parser.add_argument(
        "--state",
        type=str,
        default=None,
        help="Optional initial state as Python dict, e.g. '{\"x\": 3}'",
    )
    arg_parser.add_argument(
        "--max-steps",
        type=int,
        default=1_000_000,
        help="Maximum number of interpreted instructions",
    )
    arg_parser.add_argument(
        "--print-ir",
        action="store_true",
        help="Print generated SelVerIR before interpretation",
    )
    args = arg_parser.parse_args()

    initial_state = _parse_initial_state(args.state)
    output_ir = None
    if args.emit_ir:
        output_ir = args.output_ir if args.output_ir is not None else args.input.with_suffix(".svir")

    return run_pipeline(
        args.input,
        output_ir=output_ir,
        initial_state=initial_state,
        max_steps=args.max_steps,
        print_ir=args.print_ir,
    )
