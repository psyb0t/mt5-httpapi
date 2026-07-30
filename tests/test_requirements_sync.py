import shlex
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _requirements(path: str) -> list[str]:
    requirements = []
    for raw_line in (ROOT / path).read_text(encoding="utf-8").splitlines():
        requirement = raw_line.split("#", 1)[0].strip()
        if requirement:
            requirements.append(requirement)
    return requirements


def _start_bat_base_requirements() -> list[str]:
    marker = '"%PYDIR%\\python.exe" -m pip install '
    for raw_line in (ROOT / "scripts" / "start.bat").read_text(
        encoding="utf-8"
    ).splitlines():
        if raw_line.startswith(marker):
            command = raw_line.removeprefix(marker)
            requirements, separator, _redirect = command.partition(' > "%PIP_TMP%"')
            assert separator, (
                "base pip install command must keep its expected log redirect"
            )
            return shlex.split(requirements)
    raise AssertionError("base pip install command not found in scripts/start.bat")


def test_windows_boot_dependencies_match_tracked_api_requirements():
    assert _start_bat_base_requirements() == _requirements("requirements-api.txt")


def test_fastmcp_consumers_reject_incompatible_mcp_v2():
    for path in ("requirements-api.txt", "requirements-mcpunifier.txt"):
        mcp_requirements = [
            requirement
            for requirement in _requirements(path)
            if requirement.lower().startswith("mcp")
        ]
        assert mcp_requirements == ["mcp<2"], f"{path} must stay on the FastMCP v1 API"
