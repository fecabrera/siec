#!/bin/sh
run_echo() {
    echo "$@"
    $@ || exit 1
}

run() {
    run_echo sie build --silent $(dirname "$0") --run $@
}

run 3.14159 3.14159
run 3.14159 3.141590
run 3.14159 3
run 1.000001 1.000000