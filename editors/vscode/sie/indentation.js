const OPENING = new Set(["(", "[", "{"]);
const MATCHING = { ")": "(", "]": "[", "}": "{" };

function visualColumn(text, tabSize) {
    let column = 0;
    for (const character of text) {
        if (character === "\t") {
            column += tabSize - (column % tabSize);
        } else {
            column += 1;
        }
    }
    return column;
}

function indentationAt(column, tabSize, insertSpaces) {
    if (insertSpaces) {
        return " ".repeat(column);
    }

    const tabs = Math.floor(column / tabSize);
    return "\t".repeat(tabs) + " ".repeat(column % tabSize);
}

function scan(lines) {
    const stack = [];
    let blockComment = false;
    let lastMultilineClosed = null;

    for (let lineNumber = 0; lineNumber < lines.length; lineNumber += 1) {
        const line = lines[lineNumber];
        let quote = null;
        let escaped = false;

        for (let character = 0; character < line.length; character += 1) {
            const current = line[character];
            const next = line[character + 1];

            if (blockComment) {
                if (current === "*" && next === "/") {
                    blockComment = false;
                    character += 1;
                }
                continue;
            }

            if (quote !== null) {
                if (escaped) {
                    escaped = false;
                } else if (current === "\\") {
                    escaped = true;
                } else if (current === quote) {
                    quote = null;
                }
                continue;
            }

            if (current === "/" && next === "/") {
                break;
            }
            if (current === "/" && next === "*") {
                blockComment = true;
                character += 1;
                continue;
            }
            if (current === "\"" || current === "'") {
                quote = current;
                continue;
            }
            if (OPENING.has(current)) {
                stack.push({ character, line: lineNumber, token: current });
                continue;
            }

            const expected = MATCHING[current];
            const opener = stack[stack.length - 1];
            if (expected && opener && opener.token === expected) {
                stack.pop();
                const multiline = opener.line < lineNumber;
                if (multiline && (expected === "(" || expected === "[")) {
                    lastMultilineClosed = opener;
                }
            }
        }
    }

    return { lastMultilineClosed, stack };
}

function indentationAfter(lines, tabSize = 4, insertSpaces = true) {
    if (lines.length === 0) {
        return null;
    }

    const { lastMultilineClosed, stack } = scan(lines);
    const opener = stack[stack.length - 1];
    if (opener && (opener.token === "(" || opener.token === "[")) {
        const prefix = lines[opener.line].slice(0, opener.character + 1);
        return indentationAt(visualColumn(prefix, tabSize), tabSize, insertSpaces);
    }

    const previous = lines[lines.length - 1];
    if (lastMultilineClosed && !previous.trimEnd().endsWith("{")) {
        return lines[lastMultilineClosed.line].match(/^\s*/)[0];
    }

    return null;
}

module.exports = { indentationAfter };
