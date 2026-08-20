// Decode the LSP's delta-encoded inactive-region tokens into ordinary ranges.
// VS Code positions, like LSP positions, count UTF-16 code units.

function inactiveRanges(data) {
    const ranges = [];
    let line = 0;
    let start = 0;

    for (let offset = 0; offset + 4 < data.length; offset += 5) {
        const deltaLine = data[offset];
        line += deltaLine;
        start = deltaLine === 0 ? start + data[offset + 1] : data[offset + 1];
        ranges.push([line, start, line, start + data[offset + 2]]);
    }

    return ranges;
}

module.exports = { inactiveRanges };
