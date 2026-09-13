"""Guards which docs are published, and that published docs stay impersonal.

docs/ is split: DECISIONS.md and ARCHITECTURE.md are committed to this public
repo, while SPEC.md and WORKLOG.md live only in a separate private repository.
The published pair must never pick up personal data. A failure here means
something needs generalising before the next commit, not that the test needs
relaxing.

Refer to people by role ("the owner"). Describe machines by capability
("a consumer GPU with 16 GB VRAM"), not by model number.
"""

import re

import pytest

from app.config import BASE_DIR

PUBLISHED = [
    BASE_DIR / "docs" / "DECISIONS.md",
    BASE_DIR / "docs" / "ARCHITECTURE.md",
]

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


@pytest.mark.parametrize("doc", PUBLISHED, ids=lambda p: p.name)
@pytest.mark.parametrize("pattern, instead", FORBIDDEN)
def test_no_personal_data(doc, pattern, instead):
    if not doc.is_file():
        pytest.skip(f"{doc.name} not present")
    hits = []
    for n, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
        if re.search(pattern, line, re.IGNORECASE):
            hits.append(f"    docs/{doc.name}:{n}: {line.strip()[:90]}")
    assert not hits, (
        f"\nPersonal data in a published file (/{pattern}/). Use {instead}.\n"
        + "\n".join(hits)
    )


def test_correct_docs_are_published():
    """SPEC.md and WORKLOG.md stay private; DECISIONS.md and ARCHITECTURE.md ship."""
    import subprocess

    for path, should_be_ignored in (
        ("docs/SPEC.md", True),
        ("docs/WORKLOG.md", True),
        ("docs/DECISIONS.md", False),
        ("docs/ARCHITECTURE.md", False),
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
