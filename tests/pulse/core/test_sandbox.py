from __future__ import annotations

from pulse.core.sandbox import PythonSandbox


def test_sandbox_allows_safe_code() -> None:
    sandbox = PythonSandbox()
    code = (
        "from __future__ import annotations\n"
        "def run(x: int) -> int:\n"
        "    return x + 1\n"
    )
    result = sandbox.validate(code)
    assert result.allowed is True
    assert result.subprocess_ok is True


def test_sandbox_blocks_import_and_call() -> None:
    sandbox = PythonSandbox()
    code = (
        "import os\n"
        "def run():\n"
        "    return os.system('echo test')\n"
    )
    result = sandbox.validate(code)
    assert result.allowed is False
    assert "os" in result.blocked_imports
    assert "os.system" in result.blocked_calls


def test_sandbox_blocks_oversized_source() -> None:
    sandbox = PythonSandbox()
    code = "x = 1\n" * 5000
    result = sandbox.validate(code)
    assert result.allowed is False
    assert any("source too large" in issue for issue in result.issues)
