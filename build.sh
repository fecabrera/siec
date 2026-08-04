#!/bin/bash
run_echo() {
    echo "$@"
    $@ || exit 1
}

mkdir -p dist/

# Build examples
for dir in examples/*/*; do run_echo sie build $dir; done

run_echo pip wheel . --no-deps -w dist
