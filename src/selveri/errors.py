from __future__ import annotations

from typing import Sequence

from .diagnostics import (
    Counterexample,
    Diagnostic,
    DiagnosticCode,
    DiagnosticFix,
    DiagnosticLabel,
    DiagnosticNote,
    DiagnosticSeverity,
    SourceFile,
    SourceSpan,
    TraceEntry,
    make_diagnostic,
)


class SelVeriError(Exception):
    default_category = "Error"
    default_title = "error"
    default_code: str | None = None

    def __init__(
        self,
        diagnostic: Diagnostic | str,
        *,
        span: SourceSpan | None = None,
        code: str | None = None,
        title: str | None = None,
        message: str | None = None,
        notes: Sequence[DiagnosticNote | str] = (),
        hint: str | None = None,
        labels: Sequence[DiagnosticLabel] = (),
        fixes: Sequence[DiagnosticFix] = (),
        context: dict[str, object] | None = None,
        counterexample: Counterexample | dict[str, object] | None = None,
        severity: DiagnosticSeverity = DiagnosticSeverity.ERROR,
        category: str | None = None,
    ) -> None:
        if isinstance(diagnostic, Diagnostic):
            resolved = diagnostic
        else:
            resolved = make_diagnostic(
                code=code,
                severity=severity,
                category=category or self.default_category,
                title=title or self.default_title,
                message=message or str(diagnostic),
                span=span,
                notes=notes,
                hint=hint,
                labels=labels,
                fixes=fixes,
                context=context,
                counterexample=counterexample,
            )
            if resolved.code is None and self.default_code is not None:
                resolved = make_diagnostic(
                    code=self.default_code,
                    severity=resolved.severity,
                    category=resolved.category,
                    title=resolved.title,
                    message=resolved.message,
                    span=resolved.span,
                    notes=resolved.notes,
                    hint=resolved.hint,
                    labels=resolved.labels,
                    fixes=resolved.fixes,
                    context=resolved.context,
                    counterexample=resolved.counterexample,
                )
        self.diagnostic = resolved
        super().__init__(resolved.message)


class ParserError(SelVeriError):
    default_category = "ParseError"
    default_title = "parse error"
    default_code = DiagnosticCode.PARSE_ERROR


class CompilerError(SelVeriError):
    default_category = "CompileError"
    default_title = "compile error"
    default_code = DiagnosticCode.COMPILE_ERROR


class VerificationError(SelVeriError):
    default_category = "VerificationError"
    default_title = "verification error"
    default_code = DiagnosticCode.VERIFICATION_ERROR


class SpecDomainBoundsError(VerificationError):
    default_category = "VerificationError"
    default_title = "specification domain bounds"


class PreprocessorError(SelVeriError):
    default_category = "PreprocessorError"
    default_title = "preprocessor error"
    default_code = DiagnosticCode.PREPROCESSOR_ERROR


class VerifierRuntimeError(SelVeriError):
    default_category = "VerifierRuntimeError"
    default_title = "verifier runtime error"
    default_code = DiagnosticCode.VERIFIER_RUNTIME_ERROR


class IRRuntimeError(SelVeriError):
    default_category = "RuntimeError"
    default_title = "runtime error"
    default_code = DiagnosticCode.RUNTIME_ERROR


class IRParseError(SelVeriError):
    default_category = "IRParseError"
    default_title = "IR parse error"
    default_code = DiagnosticCode.IR_PARSE_ERROR


SelVeriParseError = ParserError
SelVeriCompileError = CompilerError


def _pretty_type(value: str | None) -> str | None:
    if value is None:
        return None
    return {"INT": "Int", "FLOAT": "Float"}.get(value, value)


def parse_error(
    message: str,
    *,
    span: SourceSpan | None = None,
    title: str = "parse error",
    notes: Sequence[DiagnosticNote | str] = (),
    hint: str | None = None,
    code: str | None = None,
    labels: Sequence[DiagnosticLabel] = (),
) -> ParserError:
    return ParserError(message, span=span, title=title, notes=notes, hint=hint, code=code, labels=labels)


def compile_error(
    message: str,
    *,
    span: SourceSpan | None = None,
    title: str = "compile error",
    notes: Sequence[DiagnosticNote | str] = (),
    hint: str | None = None,
    code: str | None = None,
    labels: Sequence[DiagnosticLabel] = (),
) -> CompilerError:
    return CompilerError(message, span=span, title=title, notes=notes, hint=hint, code=code, labels=labels)


def verification_error(
    message: str,
    *,
    span: SourceSpan | None = None,
    title: str = "verification error",
    notes: Sequence[DiagnosticNote | str] = (),
    hint: str | None = None,
    code: str | None = None,
    labels: Sequence[DiagnosticLabel] = (),
    counterexample: Counterexample | dict[str, object] | None = None,
) -> VerificationError:
    return VerificationError(
        message,
        span=span,
        title=title,
        notes=notes,
        hint=hint,
        code=code,
        labels=labels,
        counterexample=counterexample,
    )


def duplicate_declaration_error(
    name: str,
    new_span: SourceSpan,
    original_span: SourceSpan,
    original_type: str | None = None,
) -> CompilerError:
    original_type = _pretty_type(original_type)
    type_suffix = f" as `{original_type}`" if original_type else ""
    return CompilerError(
        f"variable `{name}` is already declared{type_suffix}",
        code=DiagnosticCode.COMPILE_DUPLICATE_DECLARATION,
        title="duplicate declaration",
        span=new_span,
        labels=(
            DiagnosticLabel(new_span, "declared again here", "primary"),
            DiagnosticLabel(original_span, f"original declaration of `{name}`", "secondary"),
        ),
        notes=(DiagnosticNote("previous declaration was here"),),
        hint="remove the second declaration or use a different variable name",
    )


def type_mismatch_error(
    *,
    expected: str,
    actual: str,
    span: SourceSpan,
    context: str,
    declared_span: SourceSpan | None = None,
) -> CompilerError:
    expected = _pretty_type(expected) or expected
    actual = _pretty_type(actual) or actual
    labels = [DiagnosticLabel(span, f"expression has type `{actual}`", "primary")]
    if declared_span is not None:
        labels.append(DiagnosticLabel(declared_span, "original declaration", "secondary"))
    notes: tuple[DiagnosticNote | str, ...] = ()
    if declared_span is not None:
        notes = (DiagnosticNote(f"`{context}` was declared as `{expected}` here"),)
    return CompilerError(
        f"cannot assign `{actual}` to {context} of type `{expected}`",
        code=DiagnosticCode.COMPILE_TYPE_MISMATCH,
        title="type mismatch",
        span=span,
        labels=tuple(labels),
        notes=notes,
        hint="change the expression type or declare a compatible type",
    )


def unknown_identifier_error(
    name: str,
    span: SourceSpan,
    suggestions: Sequence[str] = (),
) -> CompilerError:
    hint = f"declare `{name}` before using it"
    if suggestions:
        hint += f"; did you mean {', '.join(f'`{s}`' for s in suggestions)}?"
    return CompilerError(
        f"cannot resolve identifier `{name}`",
        code=DiagnosticCode.COMPILE_UNKNOWN_IDENTIFIER,
        title="unknown identifier",
        span=span,
        labels=(DiagnosticLabel(span, f"`{name}` is not declared", "primary"),),
        hint=hint,
    )


def invalid_list_index_error(*, span: SourceSpan, actual: str) -> CompilerError:
    actual = _pretty_type(actual) or actual
    return CompilerError(
        f"list indices must have type `Int`, but found `{actual}`",
        code=DiagnosticCode.COMPILE_INVALID_LIST_INDEX,
        title="invalid list index",
        span=span,
        labels=(DiagnosticLabel(span, "list index must be an `Int`", "primary"),),
    )


def invalid_return_type_error(
    *,
    function_name: str,
    expected: str,
    actual: str,
    span: SourceSpan,
) -> CompilerError:
    expected = _pretty_type(expected) or expected
    actual = _pretty_type(actual) or actual
    return CompilerError(
        f"function `{function_name}` must return `{expected}`",
        code=DiagnosticCode.COMPILE_INVALID_RETURN_TYPE,
        title="invalid return type",
        span=span,
        labels=(DiagnosticLabel(span, f"returned expression has type `{actual}`", "primary"),),
        hint=f"return an `{expected}` value or update the function signature",
    )


def division_by_zero_error(
    *,
    span: SourceSpan,
    denominator: str | None = None,
    value: object | None = None,
) -> IRRuntimeError:
    notes: list[DiagnosticNote | str] = []
    if denominator is not None and value is not None:
        notes.append(DiagnosticNote(f"`{denominator}` evaluated to {value}"))
    return IRRuntimeError(
        "cannot divide by zero",
        code=DiagnosticCode.RUNTIME_DIVISION_BY_ZERO,
        title="division by zero",
        span=span,
        labels=(DiagnosticLabel(span, "division denominator is zero", "primary"),),
        notes=tuple(notes),
    )


def index_out_of_bounds_error(
    *,
    name: str,
    index: int,
    length: int,
    span: SourceSpan,
) -> IRRuntimeError:
    return IRRuntimeError(
        f"valid index range for `{name}` is 0..{max(length - 1, 0)}",
        code=DiagnosticCode.RUNTIME_INDEX_OUT_OF_BOUNDS,
        title="index out of bounds",
        span=span,
        labels=(DiagnosticLabel(span, f"index is {index}", "primary"),),
        notes=(DiagnosticNote(f"`len({name})` evaluated to {length}"),),
    )


def invalid_input_value_error(
    *,
    expected: str,
    value: object,
    span: SourceSpan,
) -> IRRuntimeError:
    expected = _pretty_type(expected) or expected
    return IRRuntimeError(
        f"input value `{value}` cannot be converted to `{expected}`",
        code=DiagnosticCode.RUNTIME_INVALID_INPUT_VALUE,
        title="invalid input value",
        span=span,
        labels=(DiagnosticLabel(span, f"expected an `{expected}` input", "primary"),),
        hint=f"provide an {expected.lower()} input value",
    )


def verification_failure_error(
    *,
    spec_text: str,
    span: SourceSpan,
    counterexample: dict[str, object] | None = None,
    reason: str | None = None,
) -> VerificationError:
    return VerificationError(
        reason or f"the verifier ended up in a state where `{spec_text}` is false",
        code=DiagnosticCode.VERIFICATION_ASSERTION_FAILED,
        title="assertion failed",
        span=span,
        labels=(DiagnosticLabel(span, "specification is not guaranteed", "primary"),),
        counterexample=counterexample,
    )


def solver_unknown_error(
    *,
    spec_text: str,
    span: SourceSpan,
    reason: str | None = None,
) -> VerificationError:
    notes = (DiagnosticNote(f"reason: {reason}"),) if reason else ()
    return VerificationError(
        "the solver could not decide this formula",
        code=DiagnosticCode.VERIFICATION_SOLVER_UNKNOWN,
        title="solver returned unknown",
        span=span,
        labels=(DiagnosticLabel(span, "solver could not prove or refute this specification", "primary"),),
        notes=notes,
        hint="try using a bounded domain, simplifying the predicate, or adding an invariant",
        context={"spec_text": spec_text},
    )


def temporal_failure_error(
    *,
    spec_name: str,
    operator: str,
    span: SourceSpan,
    failing_step: int | None,
    trace: Sequence[TraceEntry] = (),
) -> VerificationError:
    code = (
        DiagnosticCode.VERIFICATION_TEMPORAL_NOT_SATISFIED
        if operator.lower() == "eventually"
        else DiagnosticCode.VERIFICATION_TEMPORAL_VIOLATED
    )
    return VerificationError(
        f"temporal property `{spec_name}` fails on the checked execution trace",
        code=code,
        title="temporal specification violated" if code.endswith("006") else "temporal specification not satisfied",
        span=span,
        labels=(DiagnosticLabel(span, "temporal property fails on the execution trace", "primary"),),
        counterexample=Counterexample({}, step=failing_step, trace=tuple(trace)),
    )


__all__ = [
    "CompilerError",
    "Counterexample",
    "Diagnostic",
    "DiagnosticCode",
    "DiagnosticFix",
    "DiagnosticLabel",
    "DiagnosticNote",
    "DiagnosticSeverity",
    "IRParseError",
    "IRRuntimeError",
    "ParserError",
    "PreprocessorError",
    "SelVeriCompileError",
    "SelVeriError",
    "SelVeriParseError",
    "SourceFile",
    "SourceSpan",
    "SpecDomainBoundsError",
    "TraceEntry",
    "VerificationError",
    "VerifierRuntimeError",
    "compile_error",
    "division_by_zero_error",
    "duplicate_declaration_error",
    "index_out_of_bounds_error",
    "invalid_input_value_error",
    "invalid_list_index_error",
    "invalid_return_type_error",
    "make_diagnostic",
    "parse_error",
    "solver_unknown_error",
    "temporal_failure_error",
    "type_mismatch_error",
    "unknown_identifier_error",
    "verification_failure_error",
    "verification_error",
]
