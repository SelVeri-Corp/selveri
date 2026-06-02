"""Document symbols from the current SelVeri AST."""
from __future__ import annotations

from lsprotocol import types

from selveri.high_level.ast import (
    Decl,
    FunctionDecl,
    If,
    Param,
    TypeDynamicList,
    TypeInt,
    TypeList,
    TypeReal,
    While,
)
from selveri.high_level.parser import parse_selveri


def _range_from_span(span: object | None) -> types.Range:
    if span is None:
        return types.Range(
            start=types.Position(line=0, character=0),
            end=types.Position(line=0, character=1),
        )
    start_line = max(getattr(span, "start_line", 1) - 1, 0)
    start_col = max(getattr(span, "start_column", 1) - 1, 0)
    end_line = max(getattr(span, "end_line", getattr(span, "start_line", 1)) - 1, 0)
    end_col = max(getattr(span, "end_column", getattr(span, "start_column", 1)) - 1, 0)
    if end_line == start_line and end_col <= start_col:
        end_col = start_col + 1
    return types.Range(
        start=types.Position(line=start_line, character=start_col),
        end=types.Position(line=end_line, character=end_col),
    )


def _type_str(type_node: object) -> str:
    if isinstance(type_node, TypeInt):
        return "Int"
    if isinstance(type_node, TypeReal):
        return "Real"
    if isinstance(type_node, TypeList):
        shape = ", ".join(str(part) for part in type_node.shape)
        suffix = f", {shape}" if shape else ""
        return f"List[{_type_str(type_node.elem)}, {type_node.dimension.value}{suffix}]"
    if isinstance(type_node, TypeDynamicList):
        shape = ", ".join(str(part) for part in type_node.shape if part is not None)
        suffix = f", {shape}" if shape else ""
        return f"List[{_type_str(type_node.elem)}, {type_node.dimension.value}{suffix}]"
    return str(type_node)


def _decl_symbol(decl: Decl) -> types.DocumentSymbol:
    symbol_range = _range_from_span(decl.span)
    return types.DocumentSymbol(
        name=decl.name,
        kind=types.SymbolKind.Variable,
        range=symbol_range,
        selection_range=symbol_range,
        detail=_type_str(decl.type_node),
    )


def _param_symbol(param: Param) -> types.DocumentSymbol:
    symbol_range = _range_from_span(param.span)
    return types.DocumentSymbol(
        name=param.name,
        kind=types.SymbolKind.Variable,
        range=symbol_range,
        selection_range=symbol_range,
        detail=f"parameter: {_type_str(param.type_node)}",
    )


def _collect_decl_symbols(stmts: list) -> list[types.DocumentSymbol]:
    result: list[types.DocumentSymbol] = []
    for stmt in stmts:
        if isinstance(stmt, Decl):
            result.append(_decl_symbol(stmt))
        elif isinstance(stmt, If):
            result.extend(_collect_decl_symbols(stmt.then_s or []))
            result.extend(_collect_decl_symbols(stmt.else_s or []))
        elif isinstance(stmt, While):
            result.extend(_collect_decl_symbols(stmt.body or []))
    return result


def _function_symbol(func: FunctionDecl) -> types.DocumentSymbol:
    symbol_range = _range_from_span(func.span)
    params = ", ".join(f"{p.name}: {_type_str(p.type_node)}" for p in func.params)
    children = [_param_symbol(param) for param in func.params]
    children.extend(_collect_decl_symbols(func.body))
    return types.DocumentSymbol(
        name=func.name,
        kind=types.SymbolKind.Function,
        range=symbol_range,
        selection_range=symbol_range,
        detail=f"({params}) -> {_type_str(func.return_type)}",
        children=children,
    )


def get_document_symbols(source: str) -> list[types.DocumentSymbol]:
    try:
        ast = parse_selveri(source)
    except Exception:
        return []

    symbols: list[types.DocumentSymbol] = []
    symbols.extend(_function_symbol(func) for func in ast.func_decls)
    symbols.extend(_decl_symbol(stmt) for stmt in ast.stmt_seq if isinstance(stmt, Decl))
    return symbols
