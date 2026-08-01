#!/bin/bash
SIEC="${SIEC:-pipenv run python -m siec}"

run_echo() {
    echo "$@"
    $@ || exit 1
}

mkdir -p dist/

# Build examples
for dir in examples/*/*; do LIBRARY_PATH=$LIBRARY_PATH:$(brew --prefix)/lib run_echo sie build $dir; done

run_echo pip wheel . --no-deps -w dist
