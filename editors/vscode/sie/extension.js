// The VSCode side of Sie support: a thin client that launches sie-lsp
// and connects it to .sie documents. All the language smarts live in
// the server (siec/lsp.py); this file only wires it up.

const vscode = require("vscode");
const { LanguageClient } = require("vscode-languageclient/node");
const { indentationAfter } = require("./indentation");

let client;

async function insertLineBreak() {
    const editor = vscode.window.activeTextEditor;
    await vscode.commands.executeCommand("type", { text: "\n" });

    if (!editor || editor.document.languageId !== "sie"
            || editor !== vscode.window.activeTextEditor) {
        return;
    }

    const tabSize = Number(editor.options.tabSize) || 4;
    const insertSpaces = editor.options.insertSpaces !== false;
    const replacements = new Map();

    for (const selection of editor.selections) {
        const lineNumber = selection.active.line;
        if (!selection.isEmpty || lineNumber === 0 || replacements.has(lineNumber)) {
            continue;
        }

        const preceding = [];
        for (let number = 0; number < lineNumber; number += 1) {
            preceding.push(editor.document.lineAt(number).text);
        }

        const indentation = indentationAfter(preceding, tabSize, insertSpaces);
        if (indentation === null) {
            continue;
        }

        const line = editor.document.lineAt(lineNumber);
        const current = line.text.match(/^\s*/)[0];
        if (current !== indentation) {
            replacements.set(lineNumber, { current, indentation });
        }
    }

    if (replacements.size === 0) {
        return;
    }

    await editor.edit((builder) => {
        for (const [lineNumber, replacement] of replacements) {
            builder.replace(
                new vscode.Range(lineNumber, 0,
                                 lineNumber, replacement.current.length),
                replacement.indentation,
            );
        }
    }, { undoStopBefore: false, undoStopAfter: false });
}

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
    context.subscriptions.push(vscode.commands.registerCommand(
        "sie.insertLineBreak", insertLineBreak,
    ));
    client.start();
}

function deactivate() {
    return client ? client.stop() : undefined;
}

module.exports = { activate, deactivate };
