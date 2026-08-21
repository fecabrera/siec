// The VSCode side of Sie support: a thin client that launches sie-lsp
// and connects it to .sie documents. All the language smarts live in
// the server (siec/lsp.py); this file only wires it up.

const vscode = require("vscode");
const { LanguageClient } = require("vscode-languageclient/node");
const { indentationAfter } = require("./indentation");
const { inactiveRanges } = require("./inactive");
const { targetFor } = require("./run-debug");
const fs = require("fs");
const path = require("path");

let client;
let inactiveDecoration;
const inactiveByDocument = new Map();

function decorateInactiveDocument(document, encoded) {
    const ranges = inactiveRanges(encoded).map(
        ([startLine, startColumn, endLine, endColumn]) => new vscode.Range(
            startLine, startColumn, endLine, endColumn,
        ),
    );
    inactiveByDocument.set(document.uri.toString(), ranges);

    for (const editor of vscode.window.visibleTextEditors) {
        if (editor.document.uri.toString() === document.uri.toString()) {
            editor.setDecorations(inactiveDecoration, ranges);
        }
    }
}

async function provideInactiveSemanticTokens(document, token, next) {
    const semanticTokens = await next(document, token);
    if (!semanticTokens) {
        return semanticTokens;
    }

    // The server's semantic tokens describe only rejected @if regions. Use
    // them as ranges for an opacity decoration, then hide the tokens from
    // VS Code so their "comment" fallback does not replace syntax colors.
    decorateInactiveDocument(document, semanticTokens.data);
    return new vscode.SemanticTokens(new Uint32Array());
}

function expandSetting(value, workspaceFolder) {
    if (typeof value !== "string") {
        return value;
    }
    return value
        .replace(/\$\{workspaceFolder\}/g, workspaceFolder || "")
        .replace(/\$\{env:([^}]+)\}/g, (_, name) => process.env[name] || "");
}

async function currentTarget(argsSetting) {
    const editor = vscode.window.activeTextEditor;
    if (!editor || editor.document.languageId !== "sie"
            || editor.document.uri.scheme !== "file") {
        throw new Error("Open a .sie file to run or debug it.");
    }
    if (editor.document.isDirty && !await editor.document.save()) {
        throw new Error("Save the current Sie file before running it.");
    }

    const folder = vscode.workspace.getWorkspaceFolder(editor.document.uri);
    const workspace = folder && folder.uri.fsPath;
    const config = vscode.workspace.getConfiguration("sie", editor.document.uri);
    const expand = (value) => expandSetting(value, workspace);
    return {
        folder,
        target: targetFor(editor.document.uri.fsPath, workspace, {
            compilerCommand: expand(config.get("compilerPath") || "siec"),
            packageCommand: expand(config.get("packageManagerPath") || "sie"),
            includePaths: (config.get("includePaths") || []).map(expand),
            args: config.get(argsSetting) || [],
        }),
    };
}

function taskFor(folder, name, invocation, cwd) {
    const scope = folder || vscode.TaskScope.Workspace;
    const execution = new vscode.ProcessExecution(
        invocation.command, invocation.args, { cwd },
    );
    const task = new vscode.Task(
        { type: "sie", task: name }, scope, name, "sie", execution, [],
    );
    task.presentationOptions = {
        reveal: vscode.TaskRevealKind.Always,
        panel: vscode.TaskPanelKind.Dedicated,
        clear: true,
    };
    return task;
}

function executeTask(task) {
    return new Promise(async (resolve, reject) => {
        let execution;
        const subscription = vscode.tasks.onDidEndTaskProcess((event) => {
            if (event.execution !== execution) {
                return;
            }
            subscription.dispose();
            if (event.exitCode === 0) {
                resolve();
            } else {
                reject(new Error(`Sie build exited with code ${event.exitCode}.`));
            }
        });
        try {
            execution = await vscode.tasks.executeTask(task);
        } catch (error) {
            subscription.dispose();
            reject(error);
        }
    });
}

async function runSie() {
    try {
        const { folder, target } = await currentTarget("runArgs");
        if (target.runBuild) {
            await executeTask(taskFor(
                folder, "Build Sie", target.runBuild, target.cwd,
            ));
        }
        await vscode.tasks.executeTask(taskFor(
            folder, "Run Sie", target.run, target.cwd,
        ));
    } catch (error) {
        vscode.window.showErrorMessage(error.message || String(error));
    }
}

function debuggerType(config) {
    const requested = config.get("debugger") || "auto";
    if (requested !== "auto") {
        return requested;
    }
    if (vscode.extensions.getExtension("vadimcn.vscode-lldb")) {
        return "lldb";
    }
    if (vscode.extensions.getExtension("ms-vscode.cpptools")) {
        return "cppdbg";
    }
    return undefined;
}

async function prepareDebugConfiguration() {
    const { folder, target } = await currentTarget("debugArgs");
    const uri = vscode.window.activeTextEditor.document.uri;
    const config = vscode.workspace.getConfiguration("sie", uri);
    const type = debuggerType(config);
    if (!type) {
        throw new Error(
            "Sie debugging requires the CodeLLDB or Microsoft C/C++ extension.",
        );
    }

    fs.mkdirSync(path.dirname(target.program), { recursive: true });
    await executeTask(taskFor(folder, "Build Sie (debug)",
                              target.build, target.cwd));

    const launch = {
        type,
        request: "launch",
        name: "Debug Sie",
        program: target.program,
        args: config.get("debugArgs") || [],
        cwd: target.cwd,
    };
    if (type === "cppdbg") {
        launch.MIMode = process.platform === "darwin" ? "lldb" : "gdb";
        launch.externalConsole = false;
        launch.stopAtEntry = false;
    }
    return { folder, launch };
}

async function debugSie() {
    try {
        const { folder, launch } = await prepareDebugConfiguration();
        const started = await vscode.debug.startDebugging(folder, launch);
        if (!started) {
            throw new Error(`Could not start the ${launch.type} debugger.`);
        }
    } catch (error) {
        vscode.window.showErrorMessage(error.message || String(error));
    }
}

const debugConfigurationProvider = {
    async provideDebugConfigurations() {
        try {
            const { launch } = await prepareDebugConfiguration();
            return [launch];
        } catch (error) {
            vscode.window.showErrorMessage(error.message || String(error));
            return [];
        }
    },
};

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
        middleware: {
            provideDocumentSemanticTokens: provideInactiveSemanticTokens,
        },
    };

    inactiveDecoration = vscode.window.createTextEditorDecorationType({
        opacity: "0.55",
    });

    client = new LanguageClient("sie", "Sie Language Server",
                                serverOptions, clientOptions);
    context.subscriptions.push(client);
    context.subscriptions.push(inactiveDecoration);
    context.subscriptions.push(vscode.window.onDidChangeVisibleTextEditors(
        (editors) => {
            for (const editor of editors) {
                const ranges = inactiveByDocument.get(editor.document.uri.toString());
                if (ranges) {
                    editor.setDecorations(inactiveDecoration, ranges);
                }
            }
        },
    ));
    context.subscriptions.push(vscode.workspace.onDidCloseTextDocument(
        (document) => inactiveByDocument.delete(document.uri.toString()),
    ));
    context.subscriptions.push(vscode.commands.registerCommand(
        "sie.insertLineBreak", insertLineBreak,
    ));
    context.subscriptions.push(vscode.commands.registerCommand("sie.run", runSie));
    context.subscriptions.push(vscode.commands.registerCommand("sie.debug", debugSie));
    context.subscriptions.push(vscode.debug.registerDebugConfigurationProvider(
        "sie", debugConfigurationProvider,
        vscode.DebugConfigurationProviderTriggerKind.Initial,
    ));
    client.start();
}

function deactivate() {
    return client ? client.stop() : undefined;
}

module.exports = { activate, deactivate };
