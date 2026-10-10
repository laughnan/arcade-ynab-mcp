"""The server authenticates only with each caller's OAuth token.

It must not read a shared credential (such as a personal access token) from the
environment, so that every tool call acts with the caller's own YNAB authorization.
"""

import re
from pathlib import Path

SRC = Path(__file__).parents[1] / "src" / "arcade_ynab"

# Environment variables, dotenv files and Arcade tool secrets (``context.get_secret``,
# ``requires_secrets``) are all ways a shared token could be read instead of the caller's.
ENVIRONMENT_ACCESS = re.compile(
    r"\bos\.environ\b|\bfrom os import\b.*\benviron\b|\bgetenv\(|\bdotenv\b"
    r"|\bget_secret\(|requires_secrets"
)


def test_source_reads_no_environment_or_secrets():
    offenders = [
        f"{path.relative_to(SRC)}:{number}: {line.strip()}"
        for path in sorted(SRC.rglob("*.py"))
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if ENVIRONMENT_ACCESS.search(line)
    ]
    assert offenders == []
