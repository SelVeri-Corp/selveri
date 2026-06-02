import * as path from "path";
import * as vscode from "vscode";
import {
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
  TransportKind,
} from "vscode-languageclient/node";

let client: LanguageClient | undefined;
let applyingUnicodeAlias = false;

const UNICODE_ALIASES: Record<string, string> = {
  "\\forall": "∀",
  "\\exists": "∃",
  "\\alpha": "α",
  "\\beta": "β",
  "\\gamma": "γ",
  "\\delta": "δ",
  "\\epsilon": "ε",
  "\\varepsilon": "ϵ",
  "\\zeta": "ζ",
  "\\eta": "η",
  "\\theta": "θ",
  "\\vartheta": "ϑ",
  "\\iota": "ι",
  "\\kappa": "κ",
  "\\varkappa": "ϰ",
  "\\lambda": "λ",
  "\\mu": "μ",
  "\\nu": "ν",
  "\\xi": "ξ",
  "\\omicron": "ο",
  "\\pi": "π",
  "\\varpi": "ϖ",
  "\\rho": "ρ",
  "\\varrho": "ϱ",
  "\\sigma": "σ",
  "\\varsigma": "ς",
  "\\tau": "τ",
  "\\upsilon": "υ",
  "\\phi": "φ",
  "\\varphi": "ϕ",
  "\\chi": "χ",
  "\\psi": "ψ",
  "\\omega": "ω",
  "\\Alpha": "Α",
  "\\Beta": "Β",
  "\\Gamma": "Γ",
  "\\Delta": "Δ",
  "\\Epsilon": "Ε",
  "\\Zeta": "Ζ",
  "\\Eta": "Η",
  "\\Theta": "Θ",
  "\\Iota": "Ι",
  "\\Kappa": "Κ",
  "\\Lambda": "Λ",
  "\\Mu": "Μ",
  "\\Nu": "Ν",
  "\\Xi": "Ξ",
  "\\Omicron": "Ο",
  "\\Pi": "Π",
  "\\Rho": "Ρ",
  "\\Sigma": "Σ",
  "\\Tau": "Τ",
  "\\Upsilon": "Υ",
  "\\Phi": "Φ",
  "\\Chi": "Χ",
  "\\Psi": "Ψ",
  "\\Omega": "Ω",
};

const MAX_UNICODE_ALIAS_LENGTH = Math.max(
  ...Object.keys(UNICODE_ALIASES).map((alias) => alias.length)
);

function registerUnicodeInput(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.workspace.onDidChangeTextDocument(async (event) => {
      if (applyingUnicodeAlias || event.document.languageId !== "selveri") {
        return;
      }

      const edits: Array<{ range: vscode.Range; replacement: string }> = [];
      const seenRanges = new Set<string>();

      for (const change of event.contentChanges) {
        if (!change.range.isEmpty || change.text.length !== 1) {
          continue;
        }

        const end = change.range.start.translate(0, change.text.length);
        const linePrefix = event.document
          .lineAt(end.line)
          .text.slice(0, end.character);
        const searchStart = Math.max(
          0,
          linePrefix.length - MAX_UNICODE_ALIAS_LENGTH
        );
        const match = linePrefix.slice(searchStart).match(/\\[A-Za-z]+$/);
        if (!match) {
          continue;
        }

        const alias = match[0];
        const replacement = UNICODE_ALIASES[alias];
        if (!replacement) {
          continue;
        }

        const start = end.translate(0, -alias.length);
        const key = `${start.line}:${start.character}:${end.character}`;
        if (seenRanges.has(key)) {
          continue;
        }
        seenRanges.add(key);
        edits.push({ range: new vscode.Range(start, end), replacement });
      }

      if (edits.length === 0) {
        return;
      }

      applyingUnicodeAlias = true;
      try {
        const workspaceEdit = new vscode.WorkspaceEdit();
        for (const edit of edits) {
          workspaceEdit.replace(event.document.uri, edit.range, edit.replacement);
        }
        await vscode.workspace.applyEdit(workspaceEdit);
      } finally {
        applyingUnicodeAlias = false;
      }
    })
  );
}

export function activate(context: vscode.ExtensionContext): void {
  const config = vscode.workspace.getConfiguration("selveri");
  const pythonPath: string = config.get("pythonPath") ?? "python";
  const serverScript = path.join(context.extensionPath, "server", "server.py");

  const serverOptions: ServerOptions = {
    command: pythonPath,
    args: [serverScript],
    transport: TransportKind.stdio,
  };

  const clientOptions: LanguageClientOptions = {
    documentSelector: [
      { scheme: "file", language: "selveri" },
      { scheme: "file", language: "selveri-ir" },
    ],
    synchronize: {
      fileEvents: vscode.workspace.createFileSystemWatcher("**/*.{svi,svir}"),
    },
    traceOutputChannel: vscode.window.createOutputChannel(
      "SelVeri Language Server Trace"
    ),
  };

  client = new LanguageClient(
    "selveri",
    "SelVeri Language Server",
    serverOptions,
    clientOptions
  );

  client.start().catch((err: unknown) => {
    vscode.window.showErrorMessage(
      `SelVeri language server failed to start: ${String(err)}. ` +
        `Check that the selveri package is installed in the Python interpreter ` +
        `at "${pythonPath}". You can change the interpreter path in settings: ` +
        `selveri.pythonPath`
    );
  });

  registerUnicodeInput(context);

  context.subscriptions.push(
    vscode.commands.registerCommand("selveri.runFile", () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor || editor.document.languageId !== "selveri") {
        vscode.window.showErrorMessage(
          "SelVeri: No active .svi file to run."
        );
        return;
      }
      const filePath = editor.document.fileName;
      const terminal = vscode.window.createTerminal("SelVeri");
      terminal.show();
      terminal.sendText(`${pythonPath} -m selveri "${filePath}"`);
    }),

    vscode.commands.registerCommand("selveri.emitIR", () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor || editor.document.languageId !== "selveri") {
        vscode.window.showErrorMessage(
          "SelVeri: No active .svi file to compile."
        );
        return;
      }
      const filePath = editor.document.fileName;
      const terminal = vscode.window.createTerminal("SelVeri IR");
      terminal.show();
      terminal.sendText(
        `${pythonPath} -m selveri "${filePath}" --emit-ir --print-ir`
      );
    }),

    vscode.commands.registerCommand("selveri.restartServer", async () => {
      if (client) {
        await client.restart();
        vscode.window.showInformationMessage(
          "SelVeri language server restarted."
        );
      }
    })
  );
}

export async function deactivate(): Promise<void> {
  if (client) {
    await client.stop();
  }
}
