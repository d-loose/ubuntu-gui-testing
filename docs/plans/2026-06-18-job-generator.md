# Job Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `job-generator` CLI that reads a YAML description of test suites/tests and emits a jenkins-job-builder (JJB) config, with one Jenkins job per test and downstream triggers wiring producer→consumer domain dependencies.

**Architecture:** A small Python package with four focused modules: `schema.py` (load + validate input YAML into a typed model), `generator.py` (pure transform from model to JJB job dicts), `jjb.py` (serialize job dicts to YAML), and `cli.py`/`__main__.py` (argument parsing and I/O wiring). A small change in the runner makes producer jobs export their chosen domain name.

**Tech Stack:** Python ≥3.12, `uv`, `PyYAML`, `argparse`, `pytest`, `pytest-mock`, `ruff`, `mypy` (strict).

## Global Constraints

- Python `>=3.12`; package/dependency manager: `uv`.
- Lint/format: `ruff`; type checking: `mypy` strict; tests: `pytest`.
- `job-generator/` ruff lint selects `["E", "F", "I", "B", "UP", "S", "SIM"]`; line-length 88; target `py312`.
- `tests/*.py` ignore `["S101", "S105", "S106", "S108"]`.
- Generated job name format: `ugt-<suite>-<test>`.
- Jenkins job repo URL (hardcoded): `https://github.com/canonical/ubuntu-gui-testing/`, branch `main`.
- ISOs resolved on the agent against `/isos`.
- Runner invoked from `runner/` via: `cd runner && uv run ubuntu-gui-testing-runner ...`, suite path `../tests/<suite>`.
- `--keep` only on producer tests; producers export domain name via property file `runner/artifacts/domain-name.txt` in format `SOURCE_DOMAIN=<name>`.
- Consumer jobs read `$SOURCE_DOMAIN` build parameter for `--source-domain`.
- Input-schema dependency field is `depends-on` (value `<suite>/<test>`). The runner CLI flag remains `--source-domain`.
- No Jenkins node labels.
- After code changes, run from each package dir: `uv run ruff format .`, `uv run ruff check .`, `uv run mypy .`, `uv run pytest tests/`.

---

### Task 1: Input schema — load and validate

**Files:**
- Modify: `job-generator/pyproject.toml` (add `PyYAML` runtime dep; add `types-PyYAML` dev dep)
- Create: `job-generator/job_generator/schema.py`
- Test: `job-generator/tests/test_schema.py`

**Interfaces:**
- Produces:
  - `class GeneratorError(Exception)`
  - `@dataclass(frozen=True) class Test` with fields `suite: str`, `name: str`, `iso: str | None`, `depends_on: str | None`; properties `key -> str` (`"<suite>/<test>"`) and `job_name -> str` (`"ugt-<suite>-<test>"`).
  - `@dataclass(frozen=True) class Config` with field `tests: tuple[Test, ...]`; methods `is_producer(test: Test) -> bool` and `consumers_of(test: Test) -> list[Test]`.
  - `def load_config(path: pathlib.Path) -> Config` — parses YAML, validates, returns `Config`. Raises `GeneratorError` on any validation failure.

- [ ] **Step 1: Add dependencies**

In `job-generator/pyproject.toml`, change the `dependencies` line and the dev group:

```toml
dependencies = ["PyYAML>=6.0"]
```

```toml
[dependency-groups]
dev = [
  "mypy>=2.1.0",
  "pytest>=9.1.0",
  "pytest-mock>=3.15.1",
  "ruff>=0.15.17",
  "types-PyYAML>=6.0",
]
```

Then run: `cd job-generator && uv sync --group dev`

- [ ] **Step 2: Write the failing tests**

Create `job-generator/tests/test_schema.py`:

```python
from pathlib import Path

import pytest

from job_generator.schema import Config, GeneratorError, Test, load_config


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "input.yaml"
    path.write_text(content)
    return path


def test_loads_iso_and_dependency_tests(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
suites:
  desktop-installer:
    tests:
      resolute.entire-disk:
        iso: ubuntu-26.04-desktop-amd64.iso
  firefox-example:
    tests:
      firefox-example-basic:
        depends-on: desktop-installer/resolute.entire-disk
""",
    )

    config = load_config(path)

    assert isinstance(config, Config)
    by_key = {t.key: t for t in config.tests}
    producer = by_key["desktop-installer/resolute.entire-disk"]
    consumer = by_key["firefox-example/firefox-example-basic"]
    assert producer.iso == "ubuntu-26.04-desktop-amd64.iso"
    assert producer.depends_on is None
    assert producer.job_name == "ugt-desktop-installer-resolute.entire-disk"
    assert consumer.depends_on == "desktop-installer/resolute.entire-disk"
    assert consumer.iso is None


def test_producer_and_consumer_relationships(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
suites:
  desktop-installer:
    tests:
      resolute.entire-disk:
        iso: x.iso
  firefox-example:
    tests:
      firefox-example-basic:
        depends-on: desktop-installer/resolute.entire-disk
""",
    )

    config = load_config(path)
    by_key = {t.key: t for t in config.tests}
    producer = by_key["desktop-installer/resolute.entire-disk"]
    consumer = by_key["firefox-example/firefox-example-basic"]

    assert config.is_producer(producer) is True
    assert config.is_producer(consumer) is False
    assert config.consumers_of(producer) == [consumer]
    assert config.consumers_of(consumer) == []


def test_error_when_both_sources(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
suites:
  s:
    tests:
      t:
        iso: x.iso
        depends-on: s/other
""",
    )
    with pytest.raises(GeneratorError, match="exactly one"):
        load_config(path)


def test_error_when_no_source(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
suites:
  s:
    tests:
      t: {}
""",
    )
    with pytest.raises(GeneratorError, match="exactly one"):
        load_config(path)


def test_error_when_dangling_reference(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
suites:
  s:
    tests:
      t:
        depends-on: s/missing
""",
    )
    with pytest.raises(GeneratorError, match="unknown test"):
        load_config(path)


def test_error_on_cycle(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
suites:
  s:
    tests:
      a:
        depends-on: s/b
      b:
        depends-on: s/a
""",
    )
    with pytest.raises(GeneratorError, match="cycle"):
        load_config(path)


def test_error_when_top_level_not_suites(tmp_path: Path) -> None:
    path = _write(tmp_path, "[1, 2, 3]\n")
    with pytest.raises(GeneratorError, match="suites"):
        load_config(path)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd job-generator && uv run pytest tests/test_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'job_generator.schema'`

- [ ] **Step 4: Write the implementation**

Create `job-generator/job_generator/schema.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class GeneratorError(Exception):
    """Raised when the input configuration is invalid."""


@dataclass(frozen=True)
class Test:
    suite: str
    name: str
    iso: str | None
    depends_on: str | None

    @property
    def key(self) -> str:
        return f"{self.suite}/{self.name}"

    @property
    def job_name(self) -> str:
        return f"ugt-{self.suite}-{self.name}"


@dataclass(frozen=True)
class Config:
    tests: tuple[Test, ...]

    def is_producer(self, test: Test) -> bool:
        return any(other.depends_on == test.key for other in self.tests)

    def consumers_of(self, test: Test) -> list[Test]:
        return [other for other in self.tests if other.depends_on == test.key]


def load_config(path: Path) -> Config:
    raw = yaml.safe_load(path.read_text())
    tests = _parse(raw)
    _validate_references(tests)
    _validate_acyclic(tests)
    return Config(tests=tuple(tests))


def _parse(raw: Any) -> list[Test]:
    if not isinstance(raw, dict) or "suites" not in raw:
        raise GeneratorError("Top-level document must be a mapping with 'suites'")
    suites = raw["suites"]
    if not isinstance(suites, dict):
        raise GeneratorError("'suites' must be a mapping of suite names")

    tests: list[Test] = []
    for suite_name, suite_body in suites.items():
        if not isinstance(suite_body, dict) or "tests" not in suite_body:
            raise GeneratorError(
                f"Suite '{suite_name}' must contain a 'tests' mapping"
            )
        suite_tests = suite_body["tests"]
        if not isinstance(suite_tests, dict):
            raise GeneratorError(
                f"'tests' in suite '{suite_name}' must be a mapping"
            )
        for test_name, test_body in suite_tests.items():
            tests.append(_parse_test(suite_name, test_name, test_body))
    return tests


def _parse_test(suite_name: str, test_name: str, body: Any) -> Test:
    body = body or {}
    if not isinstance(body, dict):
        raise GeneratorError(
            f"Test '{suite_name}/{test_name}' must be a mapping"
        )
    iso = body.get("iso")
    depends_on = body.get("depends-on")
    if (iso is None) == (depends_on is None):
        raise GeneratorError(
            f"Test '{suite_name}/{test_name}' must define exactly one of "
            "'iso' or 'depends-on'"
        )
    return Test(
        suite=suite_name,
        name=test_name,
        iso=iso,
        depends_on=depends_on,
    )


def _validate_references(tests: list[Test]) -> None:
    keys = {test.key for test in tests}
    for test in tests:
        if test.depends_on is not None and test.depends_on not in keys:
            raise GeneratorError(
                f"Test '{test.key}' depends-on unknown test "
                f"'{test.depends_on}'"
            )


def _validate_acyclic(tests: list[Test]) -> None:
    edges = {test.key: test.depends_on for test in tests}
    for start in edges:
        seen: set[str] = set()
        node: str | None = start
        while node is not None:
            if node in seen:
                raise GeneratorError(
                    f"Dependency cycle detected involving '{start}'"
                )
            seen.add(node)
            node = edges.get(node)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd job-generator && uv run pytest tests/test_schema.py -v`
Expected: PASS (7 passed)

- [ ] **Step 6: Quality gates**

Run: `cd job-generator && uv run ruff format . && uv run ruff check . && uv run mypy .`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add job-generator/pyproject.toml job-generator/uv.lock job-generator/job_generator/schema.py job-generator/tests/test_schema.py
git commit -m "feat(job-generator): add input schema loading and validation"
```

---

### Task 2: Generator — model to JJB job dicts

**Files:**
- Create: `job-generator/job_generator/generator.py`
- Test: `job-generator/tests/test_generator.py`

**Interfaces:**
- Consumes: `Config`, `Test` from `job_generator.schema`.
- Produces: `def generate_jobs(config: Config) -> list[dict[str, Any]]` — returns a list of JJB documents, each `{"job": {...}}`, in input order.

- [ ] **Step 1: Write the failing tests**

Create `job-generator/tests/test_generator.py`:

```python
from pathlib import Path

from job_generator.generator import generate_jobs
from job_generator.schema import load_config


def _config(tmp_path: Path, content: str):
    path = tmp_path / "input.yaml"
    path.write_text(content)
    return load_config(path)


def test_iso_producer_job(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        """
suites:
  desktop-installer:
    tests:
      resolute.entire-disk:
        iso: ubuntu-26.04-desktop-amd64.iso
  firefox-example:
    tests:
      firefox-example-basic:
        depends-on: desktop-installer/resolute.entire-disk
""",
    )

    jobs = generate_jobs(config)
    producer = jobs[0]["job"]

    assert producer["name"] == "ugt-desktop-installer-resolute.entire-disk"
    assert producer["scm"] == [
        {
            "git": {
                "url": "https://github.com/canonical/ubuntu-gui-testing/",
                "branches": ["main"],
            }
        }
    ]
    shell = producer["builders"][0]["shell"]
    assert "cd runner && uv run ubuntu-gui-testing-runner" in shell
    assert "--suite ../tests/desktop-installer" in shell
    assert "--test resolute.entire-disk" in shell
    assert "--iso /isos/ubuntu-26.04-desktop-amd64.iso" in shell
    assert "--keep" in shell
    assert "parameters" not in producer
    assert producer["publishers"] == [
        {
            "trigger-parameterized-builds": [
                {
                    "project": "ugt-firefox-example-firefox-example-basic",
                    "condition": "SUCCESS",
                    "property-file": "runner/artifacts/domain-name.txt",
                }
            ]
        }
    ]


def test_dependency_consumer_job(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        """
suites:
  desktop-installer:
    tests:
      resolute.entire-disk:
        iso: x.iso
  firefox-example:
    tests:
      firefox-example-basic:
        depends-on: desktop-installer/resolute.entire-disk
""",
    )

    jobs = generate_jobs(config)
    consumer = jobs[1]["job"]

    assert consumer["name"] == "ugt-firefox-example-firefox-example-basic"
    assert consumer["parameters"] == [
        {
            "string": {
                "name": "SOURCE_DOMAIN",
                "default": "",
                "description": "Domain to clone from the producer job",
            }
        }
    ]
    shell = consumer["builders"][0]["shell"]
    assert "--suite ../tests/firefox-example" in shell
    assert "--test firefox-example-basic" in shell
    assert '--source-domain "$SOURCE_DOMAIN"' in shell
    assert "--keep" not in shell
    assert "--iso" not in shell
    assert "publishers" not in consumer


def test_standalone_iso_job_has_no_keep_or_publishers(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        """
suites:
  s:
    tests:
      only:
        iso: x.iso
""",
    )

    jobs = generate_jobs(config)
    job = jobs[0]["job"]

    assert "--keep" not in job["builders"][0]["shell"]
    assert "publishers" not in job
    assert "parameters" not in job
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd job-generator && uv run pytest tests/test_generator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'job_generator.generator'`

- [ ] **Step 3: Write the implementation**

Create `job-generator/job_generator/generator.py`:

```python
from __future__ import annotations

from typing import Any

from job_generator.schema import Config, Test

REPO_URL = "https://github.com/canonical/ubuntu-gui-testing/"
BRANCH = "main"
ISO_DIR = "/isos"
PROPERTY_FILE = "runner/artifacts/domain-name.txt"


def generate_jobs(config: Config) -> list[dict[str, Any]]:
    return [{"job": _build_job(config, test)} for test in config.tests]


def _build_job(config: Config, test: Test) -> dict[str, Any]:
    is_producer = config.is_producer(test)
    job: dict[str, Any] = {"name": test.job_name}

    if test.depends_on is not None:
        job["parameters"] = [
            {
                "string": {
                    "name": "SOURCE_DOMAIN",
                    "default": "",
                    "description": "Domain to clone from the producer job",
                }
            }
        ]

    job["scm"] = [
        {"git": {"url": REPO_URL, "branches": [BRANCH]}}
    ]
    job["builders"] = [{"shell": _build_shell(test, is_producer)}]

    if is_producer:
        job["publishers"] = [
            {
                "trigger-parameterized-builds": [
                    {
                        "project": consumer.job_name,
                        "condition": "SUCCESS",
                        "property-file": PROPERTY_FILE,
                    }
                    for consumer in config.consumers_of(test)
                ]
            }
        ]

    return job


def _build_shell(test: Test, is_producer: bool) -> str:
    lines = [
        "cd runner && uv run ubuntu-gui-testing-runner \\",
        f"  --suite ../tests/{test.suite} \\",
        f"  --test {test.name} \\",
    ]
    if test.iso is not None:
        source = f"  --iso {ISO_DIR}/{test.iso}"
    else:
        source = '  --source-domain "$SOURCE_DOMAIN"'
    if is_producer:
        source += " \\"
        lines.append(source)
        lines.append("  --keep")
    else:
        lines.append(source)
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd job-generator && uv run pytest tests/test_generator.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Quality gates**

Run: `cd job-generator && uv run ruff format . && uv run ruff check . && uv run mypy .`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add job-generator/job_generator/generator.py job-generator/tests/test_generator.py
git commit -m "feat(job-generator): generate JJB job dicts from config model"
```

---

### Task 3: JJB rendering — job dicts to YAML

**Files:**
- Create: `job-generator/job_generator/jjb.py`
- Test: `job-generator/tests/test_jjb.py`

**Interfaces:**
- Consumes: list of JJB documents (`list[dict[str, Any]]`) from `generate_jobs`.
- Produces: `def render(jobs: list[dict[str, Any]]) -> str` — a single YAML document string.

- [ ] **Step 1: Write the failing tests**

Create `job-generator/tests/test_jjb.py`:

```python
import yaml

from job_generator.jjb import render


def test_render_round_trips_to_yaml() -> None:
    jobs = [
        {"job": {"name": "ugt-s-t", "builders": [{"shell": "echo hi\n"}]}}
    ]

    output = render(jobs)

    parsed = yaml.safe_load(output)
    assert parsed == jobs


def test_render_preserves_key_order() -> None:
    jobs = [
        {
            "job": {
                "name": "ugt-s-t",
                "scm": [{"git": {"url": "u", "branches": ["main"]}}],
                "builders": [{"shell": "echo hi\n"}],
            }
        }
    ]

    output = render(jobs)

    name_pos = output.index("name:")
    scm_pos = output.index("scm:")
    builders_pos = output.index("builders:")
    assert name_pos < scm_pos < builders_pos


def test_render_uses_block_scalars_for_shell() -> None:
    jobs = [
        {"job": {"name": "ugt-s-t", "builders": [{"shell": "a\nb\n"}]}}
    ]

    output = render(jobs)

    assert "|" in output
    assert "\\n" not in output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd job-generator && uv run pytest tests/test_jjb.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'job_generator.jjb'`

- [ ] **Step 3: Write the implementation**

Create `job-generator/job_generator/jjb.py`:

```python
from __future__ import annotations

from typing import Any

import yaml


class _Dumper(yaml.SafeDumper):
    pass


def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar(
            "tag:yaml.org,2002:str", data, style="|"
        )
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str_representer)


def render(jobs: list[dict[str, Any]]) -> str:
    return yaml.dump(
        jobs,
        Dumper=_Dumper,
        default_flow_style=False,
        sort_keys=False,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd job-generator && uv run pytest tests/test_jjb.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Quality gates**

Run: `cd job-generator && uv run ruff format . && uv run ruff check . && uv run mypy .`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add job-generator/job_generator/jjb.py job-generator/tests/test_jjb.py
git commit -m "feat(job-generator): render JJB job dicts to YAML"
```

---

### Task 4: CLI wiring and end-to-end

**Files:**
- Modify: `job-generator/job_generator/cli.py`
- Modify: `job-generator/job_generator/__main__.py`
- Modify: `job-generator/tests/test_cli.py`

**Interfaces:**
- Consumes: `load_config` (schema), `generate_jobs` (generator), `render` (jjb).
- Produces:
  - `def parse_args(argv: list[str] | None = None) -> argparse.Namespace` — positional `input_file: str`, optional `-o/--output: str | None`.
  - `def run(argv: list[str] | None = None) -> int` — loads, generates, renders; writes to the output path or stdout; returns `0` on success, `1` on `GeneratorError` (message to stderr).

- [ ] **Step 1: Write the failing tests**

Replace the contents of `job-generator/tests/test_cli.py` with:

```python
from pathlib import Path

import pytest

from job_generator.cli import parse_args, run


def test_parse_args_input_and_output() -> None:
    args = parse_args(["input.yaml", "-o", "out.yaml"])
    assert args.input_file == "input.yaml"
    assert args.output == "out.yaml"


def test_parse_args_defaults_output_to_none() -> None:
    args = parse_args(["input.yaml"])
    assert args.output is None


def test_run_writes_output_file(tmp_path: Path) -> None:
    input_file = tmp_path / "input.yaml"
    input_file.write_text(
        """
suites:
  desktop-installer:
    tests:
      resolute.entire-disk:
        iso: ubuntu-26.04-desktop-amd64.iso
  firefox-example:
    tests:
      firefox-example-basic:
        depends-on: desktop-installer/resolute.entire-disk
"""
    )
    output_file = tmp_path / "out.yaml"

    exit_code = run([str(input_file), "-o", str(output_file)])

    assert exit_code == 0
    content = output_file.read_text()
    assert "ugt-desktop-installer-resolute.entire-disk" in content
    assert "ugt-firefox-example-firefox-example-basic" in content
    assert "trigger-parameterized-builds" in content


def test_run_writes_to_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    input_file = tmp_path / "input.yaml"
    input_file.write_text(
        """
suites:
  s:
    tests:
      t:
        iso: x.iso
"""
    )

    exit_code = run([str(input_file)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "ugt-s-t" in out


def test_run_returns_error_on_invalid_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    input_file = tmp_path / "input.yaml"
    input_file.write_text(
        """
suites:
  s:
    tests:
      t:
        iso: x.iso
        depends-on: s/other
"""
    )

    exit_code = run([str(input_file)])

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "exactly one" in err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd job-generator && uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ImportError: cannot import name 'run' from 'job_generator.cli'`

- [ ] **Step 3: Write the CLI implementation**

Replace the contents of `job-generator/job_generator/cli.py` with:

```python
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from job_generator.generator import generate_jobs
from job_generator.jjb import render
from job_generator.schema import GeneratorError, load_config


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="job-generator",
        description="Job generator for Ubuntu GUI testing",
    )
    parser.add_argument(
        "input_file",
        help="Path to the input YAML describing suites and tests",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Path to write the JJB YAML (default: stdout)",
    )
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_config(Path(args.input_file))
    except GeneratorError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    output = render(generate_jobs(config))

    if args.output is None:
        sys.stdout.write(output)
    else:
        Path(args.output).write_text(output)
    return 0
```

- [ ] **Step 4: Update `__main__.py`**

Replace the contents of `job-generator/job_generator/__main__.py` with:

```python
import logging
import sys

from job_generator.cli import run


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    sys.exit(run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd job-generator && uv run pytest tests/ -v`
Expected: PASS (all tests across schema, generator, jjb, cli).

- [ ] **Step 6: Quality gates**

Run: `cd job-generator && uv run ruff format . && uv run ruff check . && uv run mypy .`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add job-generator/job_generator/cli.py job-generator/job_generator/__main__.py job-generator/tests/test_cli.py
git commit -m "feat(job-generator): wire CLI to generate JJB config end-to-end"
```

---

### Task 5: Runner exports domain name on `--keep`

**Files:**
- Modify: `runner/ubuntu_gui_testing_runner/base.py` (the `close()` keep branch; add `_write_domain_name_file`)
- Test: `runner/tests/test_domain_name_file.py`

**Interfaces:**
- Consumes: existing `_BaseLibvirtRunner` attributes `self.keep`, `self.domain_name`, `self.artifacts_path`.
- Produces: when `--keep` is set, a file `<artifacts_path>/domain-name.txt` containing `SOURCE_DOMAIN=<domain_name>\n`.

- [ ] **Step 1: Write the failing tests**

Create `runner/tests/test_domain_name_file.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import libvirt  # type: ignore[import-untyped]

from ubuntu_gui_testing_runner.base import _BaseLibvirtRunner


class _FakeRunner(_BaseLibvirtRunner):
    def _setup(self) -> None:
        pass

    async def _run_yarf(
        self, suite: str, test: str, vsock_cid: int, vnc_port: int
    ) -> int:
        return 0


def _conn() -> object:
    from unittest.mock import MagicMock

    conn = MagicMock()
    conn.lookupByName.side_effect = libvirt.libvirtError("not found")
    return conn


def test_writes_domain_name_file_when_keep(tmp_path: Path) -> None:
    with patch("libvirt.open", return_value=_conn()):
        runner = _FakeRunner(
            suite_name="desktop-installer",
            test_name="resolute.entire-disk",
            artifacts_dir=tmp_path,
            keep=True,
        )
        expected_name = runner.domain_name
        runner.close()

    domain_file = tmp_path / "domain-name.txt"
    assert domain_file.exists()
    assert domain_file.read_text() == f"SOURCE_DOMAIN={expected_name}\n"


def test_does_not_write_domain_name_file_without_keep(tmp_path: Path) -> None:
    with patch("libvirt.open", return_value=_conn()):
        runner = _FakeRunner(
            suite_name="desktop-installer",
            test_name="resolute.entire-disk",
            artifacts_dir=tmp_path,
            keep=False,
        )
        runner.close()

    assert not (tmp_path / "domain-name.txt").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd runner && uv run pytest tests/test_domain_name_file.py -v`
Expected: FAIL — `domain-name.txt` is not created (assertion error on `exists()`).

- [ ] **Step 3: Implement the runner change**

In `runner/ubuntu_gui_testing_runner/base.py`, in `close()`, update the `self.keep` branch to write the file. Find:

```python
        if self.keep:
            if self._domain is not None:
                self._shutdown_domain()
            LOGGER.info(
                "Keeping domain '%s' and associated resources as requested",
                self.domain_name,
            )
```

Replace with:

```python
        if self.keep:
            if self._domain is not None:
                self._shutdown_domain()
            self._write_domain_name_file()
            LOGGER.info(
                "Keeping domain '%s' and associated resources as requested",
                self.domain_name,
            )
```

Then add this method to `_BaseLibvirtRunner` (e.g. directly after `close()`):

```python
    def _write_domain_name_file(self) -> None:
        self.artifacts_path.mkdir(parents=True, exist_ok=True)
        domain_name_file = self.artifacts_path / "domain-name.txt"
        domain_name_file.write_text(f"SOURCE_DOMAIN={self.domain_name}\n")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd runner && uv run pytest tests/test_domain_name_file.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full runner suite and quality gates**

Run: `cd runner && uv run pytest tests/ && uv run ruff format . && uv run ruff check . && uv run mypy .`
Expected: all tests pass; no lint/type errors.

- [ ] **Step 6: Commit**

```bash
git add runner/ubuntu_gui_testing_runner/base.py runner/tests/test_domain_name_file.py
git commit -m "feat(runner): export chosen domain name to property file on --keep"
```

---

### Task 6: Documentation

**Files:**
- Modify: `job-generator/README.md`

- [ ] **Step 1: Update the README**

Replace the `## Usage` section of `job-generator/README.md` with:

````markdown
## Usage

Generate a jenkins-job-builder config from an input YAML:

```bash
job-generator input.yaml -o jobs.yaml
```

Omit `-o` to write to stdout.

### Input format

```yaml
suites:
  desktop-installer:
    tests:
      resolute.entire-disk:
        iso: ubuntu-26.04-desktop-amd64.iso
  firefox-example:
    tests:
      firefox-example-basic:
        depends-on: desktop-installer/resolute.entire-disk
```

Each test defines exactly one of:

- `iso: <filename>` — boot from an ISO (resolved against `/isos` on the agent).
- `depends-on: <suite>/<test>` — clone the libvirt domain produced by another
  test. The producer job runs with `--keep` and forwards the produced domain
  name to the consumer job via a Jenkins build parameter.
````

- [ ] **Step 2: Commit**

```bash
git add job-generator/README.md
git commit -m "docs(job-generator): document usage and input format"
```

---

## Self-Review Notes

- **Spec coverage:** input schema + validation (Task 1), JJB output with job names/SCM/builder/keep/params/triggers (Tasks 2–3), CLI stdout/`-o` and error handling (Task 4), runner domain-name export on keep (Task 5), docs (Task 6). All spec sections covered.
- **Type consistency:** `Test`/`Config`/`load_config`/`generate_jobs`/`render`/`run` signatures are consistent across tasks. `Test.job_name` and `Config.consumers_of`/`is_producer` used consistently in the generator.
- **Property file format** (`SOURCE_DOMAIN=<name>`) matches between Task 5 (runner writes it) and Task 2 (`property-file: runner/artifacts/domain-name.txt`).
