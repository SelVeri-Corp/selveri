from __future__ import annotations

from typing import Sequence

from .diagnostics import (
    Diagnostic,
    DiagnosticSeverity,
    SourceFile,
    SourceSpan,
    make_diagnostic,
)


class SelVeriError(Exception):
    default_category = "Error"
    default_title = "error"

    def __init__(
        self,
        diagnostic: Diagnostic | str,
        *,
        span: SourceSpan | None = None,
        code: str | None = None,
        title: str | None = None,
        message: str | None = None,
        notes: Sequence[str] = (),
        hint: str | None = None,
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
            )
        self.diagnostic = resolved
        super().__init__(resolved.message)


class ParserError(SelVeriError):
    default_category = "ParseError"
    default_title = "parse error"


class CompilerError(SelVeriError):
    default_category = "CompileError"
    default_title = "compile error"


class VerificationError(SelVeriError):
    default_category = "VerificationError"
    default_title = "verification error"


class SpecDomainBoundsError(VerificationError):
    default_category = "VerificationError"
    default_title = "specification domain bounds"


class PreprocessorError(SelVeriError):
    default_category = "PreprocessorError"
    default_title = "preprocessor error"


class VerifierRuntimeError(SelVeriError):
    default_category = "VerifierRuntimeError"
    default_title = "verifier runtime error"


class IRRuntimeError(SelVeriError):
    default_category = "RuntimeError"
    default_title = "runtime error"


class IRParseError(SelVeriError):
    default_category = "IRParseError"
    default_title = "IR parse error"


SelVeriParseError = ParserError
SelVeriCompileError = CompilerError


def parse_error(
    message: str,
    *,
    span: SourceSpan | None = None,
    title: str = "parse error",
    notes: Sequence[str] = (),
    hint: str | None = None,
) -> ParserError:
    return ParserError(message, span=span, title=title, notes=notes, hint=hint)


def compile_error(
    message: str,
    *,
    span: SourceSpan | None = None,
    title: str = "compile error",
    notes: Sequence[str] = (),
    hint: str | None = None,
) -> CompilerError:
    return CompilerError(message, span=span, title=title, notes=notes, hint=hint)


def verification_error(
    message: str,
    *,
    span: SourceSpan | None = None,
    title: str = "verification error",
    notes: Sequence[str] = (),
    hint: str | None = None,
) -> VerificationError:
    return VerificationError(message, span=span, title=title, notes=notes, hint=hint)


__all__ = [
    "CompilerError",
    "Diagnostic",
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
    "VerificationError",
    "VerifierRuntimeError",
    "compile_error",
    "make_diagnostic",
    "parse_error",
    "verification_error",
]
