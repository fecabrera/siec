"""Tests for 'sie uninstall'."""

import pytest

from siec.sie import install_root
from tests.sie.test_install import home  # noqa: F401
from tests.sie.test_list import put
from tests.sie.test_sie import run_sie


def test_a_bare_name_removes_its_only_version(
        home, monkeypatch, capsys):  # noqa: F811
    """
    One installed version makes the package name unambiguous.
    """
    package = put(install_root(), "zlib", "1.0.0")

    assert run_sie(monkeypatch, "uninstall", "zlib") == 0
    assert not package.exists()
    assert capsys.readouterr().out == "uninstalled zlib@1.0.0\n"


def test_a_bare_name_lists_multiple_versions_without_removing_them(
        home, monkeypatch, capsys):  # noqa: F811
    """
    More than one version needs an explicit choice, and every choice is
    shown in the same natural order as 'sie list'.
    """
    packages = [
        put(install_root(), "zlib", version)
        for version in ("10.0.0", "2.0.0", "9.0.0")
    ]

    assert run_sie(monkeypatch, "uninstall", "zlib") == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == [
        "sie: multiple versions detected, specify one:",
        "  zlib@2.0.0",
        "  zlib@9.0.0",
        "  zlib@10.0.0",
    ]
    assert all(package.exists() for package in packages)


def test_an_exact_spec_removes_only_that_version(
        home, monkeypatch, capsys):  # noqa: F811
    """
    'name@version' selects that install even when siblings exist.
    """
    old = put(install_root(), "zlib", "1.0.0")
    new = put(install_root(), "zlib", "2.0.0")

    assert run_sie(monkeypatch, "uninstall", "zlib@1.0.0") == 0
    assert not old.exists()
    assert new.exists()
    assert capsys.readouterr().out == "uninstalled zlib@1.0.0\n"


@pytest.mark.parametrize("flag", ["-a", "--all"])
def test_all_removes_every_installed_version(
        home, monkeypatch, capsys, flag):  # noqa: F811
    """
    Both spellings of '--all' remove each version in listing order.
    """
    for version in ("2.0.0", "1.0.0"):
        put(install_root(), "zlib", version)
    other = put(install_root(), "libc", "1.0.0")

    assert run_sie(monkeypatch, "uninstall", flag, "zlib") == 0
    assert not (install_root() / "zlib@1.0.0").exists()
    assert not (install_root() / "zlib@2.0.0").exists()
    assert other.exists()
    assert capsys.readouterr().out.splitlines() == [
        "uninstalled zlib@1.0.0",
        "uninstalled zlib@2.0.0",
    ]


def test_a_missing_package_is_an_error(
        home, monkeypatch, capsys):  # noqa: F811
    """
    Neither a bare name nor an exact spec silently succeeds when absent.
    """
    assert run_sie(monkeypatch, "uninstall", "missing") == 1
    assert capsys.readouterr().err == "sie: no missing is installed\n"

    assert run_sie(monkeypatch, "uninstall", "missing@1.0.0") == 1
    assert capsys.readouterr().err == "sie: missing@1.0.0 is not installed\n"


def test_all_rejects_an_exact_version(
        home, monkeypatch, capsys):  # noqa: F811
    """
    Combining '--all' with '@version' is contradictory and removes nothing.
    """
    package = put(install_root(), "zlib", "1.0.0")

    assert run_sie(monkeypatch, "uninstall", "--all", "zlib@1.0.0") == 1
    assert package.exists()
    assert "'--all' takes a package name" in capsys.readouterr().err


@pytest.mark.parametrize("spec", ["@1.0.0", "zlib@"])
def test_an_invalid_spec_removes_nothing(
        home, monkeypatch, capsys, spec):  # noqa: F811
    """
    Empty names and versions are rejected before the install root is read.
    """
    package = put(install_root(), "zlib", "1.0.0")

    assert run_sie(monkeypatch, "uninstall", spec) == 1
    assert package.exists()
    assert "invalid package spec" in capsys.readouterr().err
