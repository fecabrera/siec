const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const {
    findPackage,
    manifestKind,
    packageName,
    targetFor,
} = require("./run-debug");

const root = fs.mkdtempSync(path.join(os.tmpdir(), "sie-run-debug-test-"));

try {
    const sourceDirectory = path.join(root, "src");
    const source = path.join(sourceDirectory, "main.sie");
    fs.mkdirSync(sourceDirectory);
    fs.writeFileSync(source, "fn main() {}\n");

    const standalone = targetFor(source, root, {
        compilerCommand: "test-siec",
        includePaths: ["include"],
        args: ["one", "two"],
    });
    assert.strictEqual(standalone.run.command, "test-siec");
    assert.deepStrictEqual(standalone.run.args, [
        source, "-I", "include", "--run", "one", "two",
    ]);
    assert.deepStrictEqual(standalone.build.args.slice(-4), [
        "-O0", "-g", "-o", standalone.program,
    ]);

    const manifest = [
        "[package]",
        "name = \"hello\"",
        "",
        "[app]",
        "sources = [\"src/\"]",
        "",
    ].join("\n");
    fs.writeFileSync(path.join(root, "package.toml"), manifest);

    assert.strictEqual(manifestKind(manifest), "app");
    assert.strictEqual(packageName(manifest), "hello");
    assert.strictEqual(findPackage(sourceDirectory, root).directory, root);

    const packageTarget = targetFor(source, root, {
        packageCommand: "test-sie",
        args: ["argument"],
    });
    assert.deepStrictEqual(packageTarget.runBuild, {
        command: "test-sie",
        args: ["build", root],
    });
    assert.deepStrictEqual(packageTarget.run, {
        command: packageTarget.program,
        args: ["argument"],
    });
    assert.deepStrictEqual(packageTarget.build, {
        command: "test-sie",
        args: ["build", root, "-O0", "-g"],
    });
    assert.strictEqual(path.dirname(packageTarget.program), path.join(root, "build"));
} finally {
    fs.rmSync(root, { recursive: true, force: true });
}

console.log("run/debug tests passed");
