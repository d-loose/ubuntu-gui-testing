# Job Generator Design

Date: 2026-06-18

## Purpose

`job-generator` reads a YAML file describing test suites and individual tests
(including their setup requirements), and turns it into a
[jenkins-job-builder](https://jenkins-job-builder.readthedocs.io/) (JJB) YAML
config. Each generated Jenkins job runs a single test via the runner in
`runner/`. Dependencies between tests (where one test consumes a libvirt domain
produced by another) are expressed as Jenkins downstream triggers.

## Input schema

A single YAML file with an explicit, mapping-based schema:

```yaml
suites:
  desktop-installer:
    tests:
      resolute.entire-disk:
        iso: ubuntu-26.04-desktop-amd64.iso
  firefox-example:
    tests:
      firefox-example-basic:
        source-domain: desktop-installer/resolute.entire-disk
```

### Rules

- Top level is a `suites:` mapping. Each key is a suite name; its value has a
  `tests:` mapping.
- Each test maps to a config object with **exactly one** source:
  - `iso: <filename>` — boot from an ISO. The filename is resolved on the
    Jenkins agent against `/isos`.
  - `source-domain: <suite>/<test>` — clone the libvirt domain produced by
    another test in the same file.
- Having both `iso` and `source-domain`, or neither, is a validation error.
- A `source-domain` value must reference a test defined elsewhere in the same
  file. Dangling references are a validation error.
- The dependency graph formed by `source-domain` references must be acyclic.
  Cycles are a validation error.

## Generated jenkins-job-builder output

- One JJB `- job:` per test.
- Job name: `ugt-<suite>-<test>` (e.g.
  `ugt-desktop-installer-resolute.entire-disk`).
- Each job contains:
  - **SCM**: git checkout of `https://github.com/canonical/ubuntu-gui-testing/`
    (branch `main`). The repo URL is hardcoded for now.
  - **Builder** (single shell step) that invokes the runner via `uv`:

    ```bash
    cd runner && uv run ubuntu-gui-testing-runner \
      --suite ../tests/<suite> \
      --test <test> \
      <--iso /isos/<file> | --source-domain "$SOURCE_DOMAIN"> \
      [--keep]
    ```

    - `--iso /isos/<file>` is used for ISO-sourced tests.
    - `--source-domain "$SOURCE_DOMAIN"` is used for domain-sourced tests, where
      `SOURCE_DOMAIN` is a build parameter (see below).
    - `--keep` is added only when the test is a producer, i.e. it is referenced
      as a `source-domain` by at least one other test.
- **Consumer jobs** (tests with a `source-domain`) declare a `SOURCE_DOMAIN`
  string build parameter, which is consumed by `--source-domain`.
- **Producer jobs** (tests referenced by a `source-domain`) use the
  `trigger-parameterized-builds` publisher to trigger each of their consumer
  jobs on success. The `SOURCE_DOMAIN` value is forwarded from a **property
  file** at `runner/artifacts/domain-name.txt`, which the runner writes (see
  "Runner change").
- Output is a single JJB YAML document, written to stdout by default or to a
  path given by `-o/--output`.

### Node labels

No Jenkins node labels are assigned for now.

## CLI and module structure

Code lives in `job-generator/job_generator/`:

```
job_generator/
  cli.py        # argparse: input_file (positional), -o/--output
  __main__.py   # wires logging + main()
  schema.py     # load + validate YAML -> typed model (suites, tests, graph)
  generator.py  # model -> JJB job dicts (pure transform, no I/O)
  jjb.py        # render JJB dicts -> YAML (single document)
```

- `cli.py`: parses arguments. Positional `input_file` (path to the input YAML);
  `-o/--output` (optional output path, default stdout).
- `schema.py`: parses the YAML into typed dataclasses and validates it (exactly
  one source per test, references resolve, no cycles). Produces a model that
  includes, for each test, whether it is a producer and which tests it triggers.
  Raises `GeneratorError` with a clear message on any validation failure.
- `generator.py`: pure transform from the validated model to a list of JJB job
  dictionaries. No I/O, so it is straightforward to unit-test.
- `jjb.py`: serializes the JJB structure to YAML using `PyYAML` (added as a
  runtime dependency).

### Dependencies

- `PyYAML` is added to `job-generator/pyproject.toml` `dependencies`.

## Runner change (in scope)

In `runner/ubuntu_gui_testing_runner/base.py`, in `close()`, within the
`self.keep` branch: write `self.domain_name` to
`self.artifacts_path / "domain-name.txt"` in JJB property-file format:

```
SOURCE_DOMAIN=<domain_name>
```

The file is written **only** when `--keep` is set. This gives downstream
consumer jobs the exact produced domain name (which includes a date and
possible run-number suffix).

## Error handling

- Any validation error (malformed schema, both/neither source, dangling
  `source-domain` reference, dependency cycle) results in a non-zero exit code
  and a clear error message. No partial output is written.

## Testing

`job-generator/tests/`:

- `test_schema.py` — validation cases: valid input, both/neither source,
  dangling reference, cycle detection, producer/consumer derivation.
- `test_generator.py` — generated job dicts: job names, SCM, builder command,
  `--keep` only for producers, `SOURCE_DOMAIN` parameter on consumers, trigger
  publisher on producers with the property file.
- `test_cli.py` — end-to-end: input YAML file → generated JJB YAML.

`runner/tests/`:

- A test asserting `domain-name.txt` is written on `--keep` and not otherwise.

## Quality gates

Per `AGENTS.md`, run in both `job-generator/` and `runner/`:

```bash
uv run ruff format .
uv run ruff check .
uv run mypy .
uv run pytest tests/
```
