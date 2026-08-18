#!/usr/bin/env python3
"""Run `test_slipbox.py` without pytest — a bare-interpreter harness.

`test_slipbox.py` is the canonical suite (use `pytest` where it is available).
This shim emulates just the slice of pytest the suite touches — the `tmp_path`
and `monkeypatch` fixtures and the `repo` fixture — so the tests also run on a
stock Python with nothing installed. `python tests/run.py`.
"""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _install_pytest_shim() -> None:
    """Provide a no-op `pytest` module when the real one is not installed."""
    try:
        import pytest  # noqa: F401
        return
    except ImportError:
        pass
    import types
    from contextlib import contextmanager

    shim = types.ModuleType("pytest")

    def fixture(*args, **kwargs):
        # Support both @fixture and @fixture().
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]
        return lambda fn: fn

    @contextmanager
    def raises(exc, *_a, **_k):
        try:
            yield
        except exc:
            return
        raise AssertionError(f"did not raise {exc}")

    shim.fixture = fixture
    shim.raises = raises
    sys.modules["pytest"] = shim


_install_pytest_shim()


class _MonkeyPatch:
    def __init__(self):
        self._saved: list[tuple[str, str | None]] = []

    def setenv(self, name: str, value: str) -> None:
        self._saved.append((name, os.environ.get(name)))
        os.environ[name] = value

    def delenv(self, name: str, raising: bool = True) -> None:
        if name in os.environ:
            self._saved.append((name, os.environ.get(name)))
            os.environ.pop(name, None)
        elif raising:
            raise KeyError(name)

    def undo(self) -> None:
        for name, old in reversed(self._saved):
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old
        self._saved.clear()


def _make(param: str, tmp_root: Path, mp: _MonkeyPatch, repo_fixture):
    if param == "tmp_path":
        return tmp_root
    if param == "monkeypatch":
        return mp
    if param == "repo":
        return repo_fixture(tmp_root, mp)
    raise KeyError(param)


def main() -> int:
    import test_slipbox as suite

    repo_fixture = suite.repo.__wrapped__ if hasattr(suite.repo, "__wrapped__") else suite.repo
    tests = [(n, f) for n, f in vars(suite).items()
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        tmp_root = Path(tempfile.mkdtemp(prefix="slipbox-test-"))
        mp = _MonkeyPatch()
        try:
            params = inspect.signature(fn).parameters
            args = [_make(p, tmp_root, mp, repo_fixture) for p in params]
            fn(*args)
            print(f"  PASS  {name}")
            passed += 1
        except Exception:  # noqa: BLE001 - report and continue
            print(f"  FAIL  {name}")
            traceback.print_exc()
            failed += 1
        finally:
            mp.undo()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
