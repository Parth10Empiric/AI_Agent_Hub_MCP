"""
Is Phase 5 actually finished?

    python scripts/check_phase5.py

Phase5.md ends with six milestone tests and says "if all six pass,
Phase 5 is genuinely done". This walks that list and reports which of
them is covered by a test that RUNS - not by a file that exists.

The distinction matters. A test that skips because a database is
unreachable passes, and a phase declared done on the strength of
skipped tests is a phase nobody has checked.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

OK = "  OK  "
BAD = " FAIL "


# Each milestone, and the tests that actually assert it.
MILESTONES = [
    (
        "1. Permissions are enforced BELOW the model",
        "the model asks, the executor refuses, nothing reaches the network",
        [
            "tests/permissions/test_executor_gate.py",
            "tests/permissions/test_scope_policy.py",
        ],
    ),
    (
        "2. Approval survives a real workflow",
        "the turn suspends, the dialog shows real arguments, approve resumes it",
        [
            "tests/approvals/test_approval_flow.py",
            "tests/approvals/test_notifier.py",
        ],
    ),
    (
        "3. Approval cannot be bypassed",
        "someone else's id 404s, expiry denies, revoke-mid-approval denies",
        ["tests/approvals/test_approval_flow.py"],
    ),
    (
        "4. Tenants are isolated",
        "two users, two accounts, no leak - through a real MCP subprocess",
        [
            "tests/tenancy/test_credential_isolation.py",
            "tests/tenancy/test_end_to_end.py",
            "tests/permissions/test_isolation.py",
        ],
    ),
    (
        "5. Prompt injection fails safely",
        "assumes the injection WORKED, and the layers below still hold",
        [
            "tests/injection/test_layers_hold.py",
            "tests/injection/test_untrusted.py",
        ],
    ),
    (
        "6. Credentials never leak",
        "ciphertext only, redacted arguments, no response model exposes one",
        [
            "tests/credentials/test_store.py",
            "tests/credentials/test_rotation_db.py",
        ],
    ),
]


# The phases beyond the six milestones, and what proves them.
EXTRA = [
    ("5.3 OAuth: CSRF, replay, scope minimisation", "tests/oauth/"),
    ("5.7 Rate limiting: windows, budgets, login", "tests/ratelimit/"),
    ("5.8 Audit: append-only, two write modes", "tests/audit/"),
    ("5.9 Headers and the error envelope", "tests/hardening/"),
]


def run(paths: list[str]) -> tuple[int, int, int]:
    """Return (passed, failed, skipped) for a set of test files."""

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", *paths],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )

    output = result.stdout

    passed = failed = 0

    for line in output.splitlines():
        if " passed" in line or " failed" in line:
            for part in line.replace(",", "").split():
                if part.isdigit():
                    continue

            import re

            match = re.search(r"(\d+) passed", line)
            if match:
                passed = int(match.group(1))

            match = re.search(r"(\d+) failed", line)
            if match:
                failed = int(match.group(1))

    # Tests that SKIP a database body report themselves in stdout
    # rather than as pytest skips, because the zero-dependency runner
    # has no skip mechanism.
    skipped = output.count("SKIP ")

    return passed, failed, skipped


def main() -> int:

    print("Phase 5 milestones (Phase5.md)\n")

    problems: list[str] = []

    for title, what, paths in MILESTONES:

        missing = [p for p in paths if not (PROJECT_ROOT / p).exists()]

        if missing:
            print(f"{BAD} {title}")
            problems.append(f"{title}: missing {', '.join(missing)}")
            continue

        passed, failed, skipped = run(paths)

        if failed or passed == 0:
            print(f"{BAD} {title}  ({passed} passed, {failed} failed)")
            problems.append(f"{title}: {failed} failing")

        elif skipped:
            print(f"{BAD} {title}  ({passed} passed, {skipped} SKIPPED)")
            problems.append(
                f"{title}: {skipped} test(s) skipped - a skipped test "
                f"passes without checking anything. Is PostgreSQL running?"
            )

        else:
            print(f"{OK} {title}  ({passed} passed)")

        print(f"       {what}")

    print("\nBeyond the milestones\n")

    for title, path in EXTRA:

        if not (PROJECT_ROOT / path).exists():
            print(f"{BAD} {title}")
            problems.append(f"{title}: {path} does not exist")
            continue

        passed, failed, skipped = run([path])

        print(
            f"{OK if not failed and passed else BAD} {title}  "
            f"({passed} passed)"
        )

        if failed:
            problems.append(f"{title}: {failed} failing")

    print()

    if problems:
        print("NOT DONE:\n")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(
        "All six milestones pass, and nothing was skipped.\n\n"
        "Configuration is a separate question - run:\n"
        "  python scripts/check_production.py\n"
        "  python scripts/check_secrets.py"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
