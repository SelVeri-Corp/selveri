"""SelVeri pygls Language Server — registers all LSP capabilities."""
from __future__ import annotations

from lsprotocol import types
from pygls.lsp.server import LanguageServer

from . import diagnostics, hover, completion, symbols

server = LanguageServer("selveri-lsp", "v0.1.0")


def _validate(ls: LanguageServer, uri: str, source: str) -> None:
    diags = diagnostics.get_diagnostics(source)
    ls.text_document_publish_diagnostics(
        types.PublishDiagnosticsParams(uri=uri, diagnostics=diags)
    )


@server.feature(types.TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: LanguageServer, params: types.DidOpenTextDocumentParams) -> None:
    _validate(ls, params.text_document.uri, params.text_document.text)


@server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: LanguageServer, params: types.DidChangeTextDocumentParams) -> None:
    source = params.content_changes[-1].text
    _validate(ls, params.text_document.uri, source)


@server.feature(types.TEXT_DOCUMENT_DID_SAVE)
def did_save(ls: LanguageServer, params: types.DidSaveTextDocumentParams) -> None:
    doc = ls.workspace.get_text_document(params.text_document.uri)
    _validate(ls, params.text_document.uri, doc.source)


@server.feature(types.TEXT_DOCUMENT_HOVER)
def on_hover(
    ls: LanguageServer, params: types.HoverParams
) -> types.Hover | None:
    doc = ls.workspace.get_text_document(params.text_document.uri)
    return hover.get_hover(doc.source, params.position)


@server.feature(
    types.TEXT_DOCUMENT_COMPLETION,
    types.CompletionOptions(trigger_characters=[" ", ":"]),
)
def on_completion(
    ls: LanguageServer, params: types.CompletionParams
) -> types.CompletionList:
    doc = ls.workspace.get_text_document(params.text_document.uri)
    items = completion.get_completions(doc.source, params.position)
    return types.CompletionList(is_incomplete=False, items=items)


@server.feature(types.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
def on_document_symbol(
    ls: LanguageServer, params: types.DocumentSymbolParams
) -> list[types.DocumentSymbol]:
    doc = ls.workspace.get_text_document(params.text_document.uri)
    return symbols.get_document_symbols(doc.source)


def main() -> None:
    server.start_io()
