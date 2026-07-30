import shlex
from pathlib import Path

from mcp.server.fastmcp import FastMCP


_ROOT = Path(__file__).resolve().parents[1]
_MCP_SDK_REQUIREMENT = "mcp==1.28.0"


def _requirements(path: str) -> list[str]:
    requirements = []
    for raw_line in (_ROOT / path).read_text(encoding="utf-8").splitlines():
        requirement = raw_line.split("#", 1)[0].strip()
        if requirement:
            requirements.append(requirement)
    return requirements


def _windows_boot_requirements() -> list[str]:
    marker = '"%PYDIR%\\python.exe" -m pip install '
    for raw_line in (_ROOT / "scripts" / "start.bat").read_text(
        encoding="utf-8"
    ).splitlines():
        if not raw_line.startswith(marker):
            continue

        command = raw_line.removeprefix(marker)
        requirements, separator, _redirect = command.partition(' > "%PIP_TMP%"')
        assert separator, "base pip install command must retain its log redirect"
        return shlex.split(requirements)

    raise AssertionError("base pip install command not found in scripts/start.bat")


def test_windows_boot_dependencies_match_api_requirements():
    assert _windows_boot_requirements() == _requirements("requirements-api.txt")


def test_mcp_consumers_pin_compatible_sdk():
    for path in ("requirements-api.txt", "requirements-mcpunifier.txt"):
        mcp_requirements = [
            requirement
            for requirement in _requirements(path)
            if requirement.lower().startswith("mcp")
        ]
        assert mcp_requirements == [_MCP_SDK_REQUIREMENT]


def test_fastmcp_v1_api_is_importable():
    assert FastMCP.__name__ == "FastMCP"
