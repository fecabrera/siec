const fs = require("fs");
const os = require("os");
const path = require("path");
const crypto = require("crypto");

function manifestKind(contents) {
    if (/^\s*\[app\]\s*(?:#.*)?$/m.test(contents)) {
        return "app";
    }
    if (/^\s*\[library\]\s*(?:#.*)?$/m.test(contents)) {
        return "library";
    }
    return undefined;
}

function packageName(contents) {
    let inPackage = false;
    for (const line of contents.split(/\r?\n/)) {
        const section = line.match(/^\s*\[([^\]]+)\]\s*(?:#.*)?$/);
        if (section) {
            inPackage = section[1].trim() === "package";
            continue;
        }
        if (inPackage) {
            const name = line.match(/^\s*name\s*=\s*["']([^"']+)["']/);
            if (name) {
                return name[1];
            }
        }
    }
    return undefined;
}

function findPackage(start, boundary) {
    let directory = path.resolve(start);
    const limit = boundary ? path.resolve(boundary) : path.parse(directory).root;

    while (true) {
        const manifest = path.join(directory, "package.toml");
        if (fs.existsSync(manifest)) {
            const contents = fs.readFileSync(manifest, "utf8");
            return { directory, manifest, contents, kind: manifestKind(contents) };
        }
        if (directory === limit || directory === path.parse(directory).root) {
            return undefined;
        }
        const parent = path.dirname(directory);
        if (parent === directory || !directory.startsWith(`${limit}${path.sep}`)) {
            return undefined;
        }
        directory = parent;
    }
}

function executableName(name) {
    return process.platform === "win32" ? `${name}.exe` : name;
}

function standaloneDebugOutput(source) {
    const identity = crypto.createHash("sha1").update(path.resolve(source))
        .digest("hex").slice(0, 12);
    const stem = path.basename(source, path.extname(source)) || "program";
    return path.join(os.tmpdir(), "sie-vscode-debug", identity,
                     executableName(stem));
}

function targetFor(source, workspace, options = {}) {
    const packageInfo = findPackage(path.dirname(source), workspace);
    if (packageInfo && packageInfo.kind === "app") {
        const name = packageName(packageInfo.contents);
        if (!name) {
            throw new Error(`${packageInfo.manifest}: [package] needs a name`);
        }
        if (path.basename(name) !== name || name === "." || name === "..") {
            throw new Error(`${packageInfo.manifest}: invalid package name ${name}`);
        }
        return {
            package: packageInfo,
            cwd: packageInfo.directory,
            program: path.join(packageInfo.directory, "build", executableName(name)),
            run: {
                command: options.packageCommand || "sie",
                args: ["build", packageInfo.directory, "--run",
                       ...(options.args || [])],
            },
            build: {
                command: options.packageCommand || "sie",
                args: ["build", packageInfo.directory, "-O0", "-g"],
            },
        };
    }

    const output = standaloneDebugOutput(source);
    const includeArgs = (options.includePaths || [])
        .flatMap((includePath) => ["-I", includePath]);
    return {
        cwd: path.dirname(source),
        program: output,
        run: {
            command: options.compilerCommand || "siec",
            args: [source, ...includeArgs, "--run", ...(options.args || [])],
        },
        build: {
            command: options.compilerCommand || "siec",
            args: [source, ...includeArgs, "-O0", "-g", "-o", output],
        },
    };
}

module.exports = {
    findPackage,
    manifestKind,
    packageName,
    standaloneDebugOutput,
    targetFor,
};
