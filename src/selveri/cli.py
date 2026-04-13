from __future__ import annotations

import argparse
from pathlib import Path

from .compiler import SelVeriCompiler
from .interpreter import interpret_ir_code
from .parser import parse_selveri
from .SelVerifier.verifier import VerificationEngine

def run_pipeline(
    input_path: Path,
    *,
    output_ir: Path | None,
    max_steps: int,
    print_ir: bool,
) -> int:
    with input_path.open("r", encoding="utf-8") as f:
        source = f.read()

    program = parse_selveri(source)
    compiler = SelVeriCompiler()
    code = compiler.compile_program(program)
    ir_text = "\n".join(instr.render() for instr in code)

    if output_ir is not None:
        output_ir.parent.mkdir(parents=True, exist_ok=True)
        output_ir.write_text(ir_text, encoding="utf-8")

    if print_ir:
        print("Generated SelVerIR:")
        print(ir_text)
        print()

    verifier = VerificationEngine()
    verifier.prepare_program(code, raw_specs=compiler.raw_specs.values())
    result = interpret_ir_code(code, verifier=verifier, max_steps=max_steps)

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

    output_ir = None
    if args.emit_ir:
        output_ir = args.output_ir if args.output_ir is not None else args.input.with_suffix(".svir")

    return run_pipeline(
        args.input,
        output_ir=output_ir,
        max_steps=args.max_steps,
        print_ir=args.print_ir,
    )
