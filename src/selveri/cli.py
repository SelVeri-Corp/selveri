from __future__ import annotations

import argparse
from pathlib import Path
from time import perf_counter

from .compiler import compile_selveri_source_to_ir_text
from .interpreter import interpret_ir_text

def run_pipeline(
    input_path: Path,
    *,
    output_ir: Path | None,
    max_steps: int,
) -> int:
    with input_path.open("r", encoding="utf-8") as f:
        source = f.read()

    start_time = perf_counter()
    ir_text = compile_selveri_source_to_ir_text(source)
    end_time = perf_counter()
    print(f"Compilation time: {end_time - start_time:.6f} seconds")

    if output_ir is not None:
        output_ir.parent.mkdir(parents=True, exist_ok=True)
        output_ir.write_text(ir_text, encoding="utf-8")

    start_time = perf_counter()
    interpret_ir_text(ir_text, max_steps=max_steps)
    end_time = perf_counter()
    print(f"\nRunning time: {end_time - start_time:.6f} seconds")
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
    args = arg_parser.parse_args()

    output_ir = None
    if args.emit_ir:
        output_ir = args.output_ir if args.output_ir is not None else args.input.with_suffix(".svir")

    return run_pipeline(
        args.input,
        output_ir=output_ir,
        max_steps=args.max_steps,
    )
