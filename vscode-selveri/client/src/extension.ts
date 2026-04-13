import * as path from "path";
import * as vscode from "vscode";
import {
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
  TransportKind,
} from "vscode-languageclient/node";

let client: LanguageClient | undefined;

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
    documentSelector: [{ scheme: "file", language: "selveri" }],
    synchronize: {
      fileEvents: vscode.workspace.createFileSystemWatcher("**/*.svi"),
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
