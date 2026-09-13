"""docs/DECISIONS.md is published; docs/SPEC.md is not.

DECISIONS.md is the one file in docs/ that gets committed to a public repo, so
it must never pick up personal data. This test is the guard - a failure here
means something needs generalising before the next commit, not that the test
needs relaxing.

Refer to people by role ("the owner"). Describe machines by capability
("a consumer GPU with 16 GB VRAM"), not by model number.
"""

import re

import pytest

from app.config import BASE_DIR

DECISIONS = BASE_DIR / "docs" / "DECISIONS.md"

# Case-insensitive. Each entry is (pattern, what to write instead).
FORBIDDEN = [
    (r"\bchristian\b",          "'the owner'"),
    (r"\bwilliams\b",           "'the owner'"),
    (r"\bcwill713\b",           "'this repository'"),
    (r"\bEMFla\b",              "a relative path"),
    (r"\bgirlfriend\b",         "'the second user' or 'two people'"),
    (r"\bryzen\b",              "a capability description"),
    (r"\bradeon\b",             "a capability description"),
    (r"\b7800\s?x?3?d?\b",      "a capability description"),
    (r"\bgeforce\b|\brtx\b",    "a capability description"),
    (r"\b31\s?GB\s+RAM\b",      "a capability description"),
    (r"[\w.+-]+@[\w-]+\.[\w.]+", "no email addresses"),
    (r"github\.com/(?!\s)[\w-]+/", "'this repository'"),
]


@pytest.mark.skipif(not DECISIONS.is_file(), reason="DECISIONS.md not present")
@pytest.mark.parametrize("pattern, instead", FORBIDDEN)
def test_no_personal_data(pattern, instead):
    text = DECISIONS.read_text(encoding="utf-8")
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        if re.search(pattern, line, re.IGNORECASE):
            hits.append(f"    docs/DECISIONS.md:{n}: {line.strip()[:90]}")
    assert not hits, (
        f"\nPersonal data in a public file (/{pattern}/). Use {instead}.\n"
        + "\n".join(hits)
    )


@pytest.mark.skipif(not DECISIONS.is_file(), reason="DECISIONS.md not present")
def test_spec_stays_private():
    """SPEC.md must stay gitignored; only DECISIONS.md is published."""
    import subprocess

    for path, should_be_ignored in (
        ("docs/SPEC.md", True),
        ("docs/DECISIONS.md", False),
    ):
        ignored = (
            subprocess.run(
                ["git", "check-ignore", "-q", path],
                cwd=BASE_DIR,
                capture_output=True,
            ).returncode
            == 0
        )
        assert ignored is should_be_ignored, (
            f"{path} is {'ignored' if ignored else 'tracked'}, "
            f"expected {'ignored' if should_be_ignored else 'tracked'}"
        )
