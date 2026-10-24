"""The PHP connector must sign exactly like the hub verifies (INTEGRATION_GUIDE.md §3.2)."""

import shutil
import subprocess
from pathlib import Path

import pytest

from app.core import signing

PHP = shutil.which("php")
CONNECTOR = Path(__file__).parents[1] / "connectors" / "php" / "LazulaAiClient.php"

CASES = [
    (
        "POST",
        "/v1/tasks",
        "1761300000",
        "rq_0123456789abcdef",
        '{"task":"sermon.captions","x":"é"}',
    ),
    ("GET", "/v1/usage?from=2026-10-01&to=2026-10-31", "1761300001", "rq_abcdefgh", ""),
]


@pytest.mark.skipif(PHP is None, reason="PHP is not installed")
@pytest.mark.parametrize(("method", "path", "ts", "rid", "body"), CASES)
def test_php_and_python_signatures_match(
    method: str, path: str, ts: str, rid: str, body: str
) -> None:
    secret = "test-secret-not-real"
    script = (
        f"require {str(CONNECTOR)!r};"
        "echo LazulaAiClient::signature("
        "$argv[1], $argv[2], $argv[3], $argv[4], $argv[5], $argv[6]);"
    )
    result = subprocess.run(  # fixed arguments, no shell
        [PHP, "-r", script, secret, method, path, ts, rid, body],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    expected = signing.sign(secret.encode(), method, path, ts, rid, body.encode())
    assert result.stdout.strip() == expected


@pytest.mark.skipif(PHP is None, reason="PHP is not installed")
def test_php_connector_has_no_syntax_errors() -> None:
    result = subprocess.run([PHP, "-l", str(CONNECTOR)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
