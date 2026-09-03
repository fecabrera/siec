"""Command-line options shared by the compiler and package manager."""

import argparse


def add_compiler_options(parser: argparse.ArgumentParser) -> None:
    """Add compiler options that ``siec`` and ``sie build`` both accept."""
    parser.add_argument(
        "-O", default=0, type=int, choices=[0, 1, 2, 3], dest="opt",
        metavar="N", help="optimization level, cc-style (default 0)")
    parser.add_argument(
        "-g", "--debug", action="store_true", dest="debug",
        help="emit DWARF debug info, for source-level debugging")
    parser.add_argument(
        "-Wunchecked-dereference", action="store_true",
        dest="warn_unchecked_dereference",
        help="warn when a nullable pointer is dereferenced "
             "without a non-null proof")


def compiler_arguments(options: argparse.Namespace) -> list[str]:
    """Convert parsed shared options into arguments accepted by ``siec``."""
    arguments: list[str] = []

    if options.opt:
        arguments.append(f"-O{options.opt}")
    if options.debug:
        arguments.append("-g")
    if options.warn_unchecked_dereference:
        arguments.append("-Wunchecked-dereference")

    return arguments
