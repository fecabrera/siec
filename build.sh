#!/bin/bash
SIEC="${SIEC:-pipenv run python -m siec}"
SIE_FLAGS="${SIE_FLAGS:-}"
SIE_INCLUDES=("packages/libc/src" "packages/posix/src" "packages/tomlc17/src" "packages/core/src" "packages/mpdecimal/src", "packages/zlib/src")
SIE_LIB_DIRS=("$(brew --prefix)/lib")
SIE_LINK_LIBS=("mpdec" "z")
SIE_LIB_OBJS=("dist/libtomlc17.a")

run_echo() {
    echo "$@"
    $@ || exit 1
}

mkdir -p dist/
mkdir -p dist/bin/

# Build tomlc17
run_echo cd tomlc17
run_echo make
run_echo cp src/libtomlc17.a ../dist/
run_echo cd ..

# Build sie
run_echo $SIEC \
    "${SIE_INCLUDES[@]/#/-I }"\
    "${SIE_LIB_DIRS[@]/#/-L }"\
    "${SIE_LINK_LIBS[@]/#/-l }"\
    ${SIE_FLAGS}\
    sie/src/*.sie\
    ${SIE_LIB_OBJS[@]}\
    -o dist/bin/sie

run_echo pip wheel . --no-deps -w dist
