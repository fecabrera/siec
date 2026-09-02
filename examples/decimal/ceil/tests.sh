#!/bin/sh
run_echo() {
    echo "$@"
    $@ || exit 1
}

run() {
    run_echo sie build --silent $(dirname "$0") --run $@
}

run 3.14159