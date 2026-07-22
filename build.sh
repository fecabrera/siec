#!/bin/bash
SIEC="${SIEC:-pipenv run python -m siec}"
SIE_FLAGS="${SIE_FLAGS:-}"
SIE_INCLUDES=("packages/libc/src" "packages/posix/src" "packages/tomlc17/src" "packages/core/src" "packages/mpdecimal/src" "packages/zlib/src")
SIE_LIB_DIRS=("$(brew --prefix)/lib")
SIE_LINK_LIBS=("mpdec" "z")
SIE_LIB_OBJS=("dist/libtomlc17.a")

run_echo() {
    echo "$@"
    $@ || exit 1
}

mkdir -p dist/

# Build tomlc17
run_echo cd tomlc17
run_echo make
run_echo cp src/libtomlc17.a ../dist/
run_echo cd ..

# Build sie
mkdir -p dist/bin/
run_echo $SIEC \
    "${SIE_INCLUDES[@]/#/-I }"\
    "${SIE_LIB_DIRS[@]/#/-L }"\
    "${SIE_LINK_LIBS[@]/#/-l }"\
    ${SIE_FLAGS}\
    sie/src/*.sie\
    ${SIE_LIB_OBJS[@]}\
    -o dist/bin/sie

# Build examples
for pkg in $(find packages -type d -mindepth 1 -maxdepth 1); do
    mkdir -p dist/$(basename $pkg)/examples/
    for f in $(find $pkg/examples -mindepth 1 -maxdepth 1 -name "*.sie"); do
        run_echo $SIEC \
            "${SIE_INCLUDES[@]/#/-I }"\
            "${SIE_LIB_DIRS[@]/#/-L }"\
            "${SIE_LINK_LIBS[@]/#/-l }"\
            ${SIE_FLAGS}\
            $f \
            ${SIE_LIB_OBJS[@]}\
            -o dist/$(basename -- $pkg)/examples/$(basename -- ${f%.sie}) 
    done
    
    for dir in $(find $pkg/examples -type d -mindepth 1 -maxdepth 1); do
        mkdir -p dist/$(basename $pkg)/examples/$(basename $dir)/
        for f in $(find $dir -mindepth 1 -maxdepth 1 -name "*.sie"); do
            run_echo $SIEC \
                "${SIE_INCLUDES[@]/#/-I }"\
                "${SIE_LIB_DIRS[@]/#/-L }"\
                "${SIE_LINK_LIBS[@]/#/-l }"\
                ${SIE_FLAGS}\
                $f \
                ${SIE_LIB_OBJS[@]}\
                -o dist/$(basename -- $pkg)/examples/$(basename -- $dir)/$(basename -- ${f%.sie})
        done
    done
done

run_echo pip wheel . --no-deps -w dist
