from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

"""
Zero-dependency test runner.

pytest is the right tool and the test files here are written as plain
pytest tests - `pytest tests/` works once it is installed. But pytest
is not currently in this environment, and a test suite you cannot run
today is a test suite that rots.

So: every test is a module-level function named `test_*` that uses bare
`assert`. That is valid pytest AND runnable by the 60 lines below. When
you add pytest to requirements, delete nothing - just run pytest
instead.

Usage:

    python tests/run_tests.py
    python tests/run_tests.py router
"""


def load_module(path: Path):
    name = f"_tests_{path.stem}_{abs(hash(str(path))) % 10000}"

    spec = importlib.util.spec_from_file_location(name, path)

    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def main() -> int:

    pattern = sys.argv[1] if len(sys.argv) > 1 else ""

    tests_dir = Path(__file__).resolve().parent

    files = sorted(
        path
        for path in tests_dir.rglob("test_*.py")
        if pattern in str(path.relative_to(tests_dir))
    )

    passed = 0
    failures: list[tuple[str, str]] = []

    for path in files:

        relative = path.relative_to(PROJECT_ROOT)

        try:
            module = load_module(path)

        except Exception:
            failures.append(
                (f"{relative} (import)", traceback.format_exc())
            )
            print(f"\n{relative}\n  IMPORT FAILED")
            continue

        names = sorted(
            name
            for name in dir(module)
            if name.startswith("test_")
            and callable(getattr(module, name))
        )

        if not names:
            continue

        print(f"\n{relative}")

        for name in names:

            try:
                getattr(module, name)()
                passed += 1
                print(f"  PASS  {name}")

            except Exception:
                failures.append(
                    (f"{relative}::{name}", traceback.format_exc())
                )
                print(f"  FAIL  {name}")

    print("\n" + "=" * 62)

    for label, trace in failures:
        print(f"\nFAILED {label}\n")
        print(trace)

    print(
        f"\n{passed} passed, {len(failures)} failed "
        f"({len(files)} files)"
    )

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
