// The VSCode side of Sie support: a thin client that launches sie-lsp
// and connects it to .sie documents. All the language smarts live in
// the server (siec/lsp.py); this file only wires it up.

const vscode = require("vscode");
const { LanguageClient } = require("vscode-languageclient/node");

let client;

function activate(context) {
    const config = vscode.workspace.getConfiguration("sie");

    const serverOptions = {
        command: config.get("serverPath") || "sie-lsp",
        args: [],
    };

    const clientOptions = {
        documentSelector: [{ scheme: "file", language: "sie" }],
        initializationOptions: {
            includePaths: config.get("includePaths") || [],
        },
    };

    client = new LanguageClient("sie", "Sie Language Server",
                                serverOptions, clientOptions);
    context.subscriptions.push(client);
    client.start();
}

function deactivate() {
    return client ? client.stop() : undefined;
}

module.exports = { activate, deactivate };
