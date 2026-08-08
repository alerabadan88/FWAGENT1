"""Guards on what actually ships.

These exist because of a defect that every other test was blind to:
`package-data` listed `templates/*.j2`, which does not recurse, so a built
wheel contained 3 of 13 templates and not one driver. Running from the repo
worked perfectly; the installed package could not generate firmware at all.

A test that reads the packaging config and compares it against the templates
on disk catches that without building a wheel.
"""

import fnmatch
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = ROOT / "codegen" / "templates"


@pytest.fixture(scope="module")
def pyproject():
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _packaged_patterns(pyproject) -> list[str]:
    return pyproject["tool"]["setuptools"]["package-data"]["codegen"]


def test_every_template_on_disk_would_be_packaged(pyproject):
    """A template the build does not pick up is a template the user does not get."""
    patterns = _packaged_patterns(pyproject)
    templates = sorted(p.relative_to(ROOT / "codegen") for p in TEMPLATE_DIR.rglob("*.j2"))

    assert templates, "no templates found; the layout must have moved"

    missed = [
        str(path) for path in templates
        if not any(fnmatch.fnmatch(path.as_posix(), pattern) for pattern in patterns)
    ]

    assert not missed, (
        f"these templates exist but no package-data pattern matches them, so a "
        f"wheel would ship without them: {missed}"
    )


def test_the_driver_templates_are_covered_specifically(pyproject):
    """The subdirectory is the case that broke; keep it named."""
    patterns = _packaged_patterns(pyproject)

    assert any("drivers" in pattern for pattern in patterns), (
        "no pattern mentions templates/drivers, and a bare templates/*.j2 does "
        "not recurse into it"
    )


def test_the_cli_module_is_declared(pyproject):
    """cli.py sits at the top level, so it needs declaring or it is left out."""
    modules = pyproject["tool"]["setuptools"].get("py-modules", [])

    assert "cli" in modules
    assert (ROOT / "cli.py").is_file()


def test_the_entry_point_targets_something_that_exists(pyproject):
    scripts = pyproject["project"]["scripts"]

    assert "fw-agent" in scripts
    module, _, function = scripts["fw-agent"].partition(":")

    imported = __import__(module)
    assert callable(getattr(imported, function)), (
        f"the entry point names {module}:{function}, which is not callable"
    )


def test_the_model_sdk_is_not_required_to_generate_firmware(pyproject):
    """Generating and building must not drag in a network client."""
    required = " ".join(pyproject["project"]["dependencies"]).lower()

    assert "anthropic" not in required, (
        "the Anthropic SDK belongs in the optional 'agent' extra: only the "
        "interview agent talks to a model, and nothing else should need it"
    )
    assert "anthropic" in " ".join(
        pyproject["project"]["optional-dependencies"]["agent"]
    ).lower()


def test_generation_works_without_the_model_sdk_importable(monkeypatch):
    """Prove the split above is real, not just declared."""
    import builtins

    from codegen.generator import generate_firmware
    from core.eda_parser import parse_config_file

    real_import = builtins.__import__

    def refuse_anthropic(name, *args, **kwargs):
        if name == "anthropic" or name.startswith("anthropic."):
            raise ImportError("anthropic is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse_anthropic)

    analysis = parse_config_file(ROOT / "examples" / "arduino-uno" / "config.json")
    firmware = generate_firmware(analysis)

    assert "main.c" in firmware.files
