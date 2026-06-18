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
        depends-on: desktop-installer/resolute.entire-disk
```

### Rules

- Top level is a `suites:` mapping. Each key is a suite name; its value has a
  `tests:` mapping.
- Each test maps to a config object with **exactly one** source:
  - `iso: <filename>` — boot from an ISO. The filename is resolved on the
    Jenkins agent against `/isos`.
  - `depends-on: <suite>/<test>` — clone the libvirt domain produced by
    another test in the same file.
- Having both `iso` and `depends-on`, or neither, is a validation error.
- A `depends-on` value must reference a test defined elsewhere in the same
  file. Dangling references are a validation error.
- The dependency graph formed by `depends-on` references must be acyclic.
  Cycles are a validation error.

## Generated jenkins-job-builder output

To avoid repeating the shared SCM and shell skeleton in every job, the output
is structured as a single job template plus one project:

- A single `- job-template:` named `ugt-{suite}-{test}` holds the shared **SCM**
  (git checkout of `https://github.com/canonical/ubuntu-gui-testing/`, branch
  `main`; the repo URL is hardcoded for now), a `YARF_REF` string parameter
  (default `main`), a first builder that provisions `yarf`, the shared runner
  shell skeleton, and a `triggers: '{obj:triggers}'` slot. The SCM lives in the
  template (not a global `defaults` block) so the output can be dropped into a
  larger collection of JJB files without overriding the collection's shared
  `global` defaults.

  The yarf setup builder clones `https://github.com/canonical/yarf` (ref
  `$YARF_REF`) into `$WORKSPACE/yarf` and runs `uv sync`; the runner builder
  then prepends `$WORKSPACE/yarf/.venv/bin` to `PATH` so the bare `yarf` the
  runner spawns resolves to that workspace build. See
  [the yarf setup builder design](2026-06-18-yarf-setup-builder-design.md).

    ```bash
    export PATH="$WORKSPACE/yarf/.venv/bin:$PATH"
    cd runner && uv run ubuntu-gui-testing-runner \
      --suite ../tests/{suite} \
      --test {test} \
      {args}
    ```

- A single `- project:` named `ugt` instantiates the template once per test,
  supplying `suite`, `test`, `args`, and `triggers`:
  - `args` is `--iso /isos/<file>` for ISO-sourced tests, or
    `--source-domain-prefix ugt-<psuite>-<ptest>` for domain-sourced tests
    (where `ugt-<psuite>-<ptest>` is the producer's job name and domain prefix;
    the runner resolves the most recent matching domain itself). When the test
    is a producer (referenced as a `depends-on` by at least one other test),
    ` \`+`--keep` is appended so its domain survives.
  - `triggers` is `[]` for ISO-sourced tests, or a single `reverse` trigger on
    the producer's job (`result: success`) for domain-sourced tests, so the
    consumer runs after the producer succeeds. No parameters are passed; the
    producer's domain is discovered by prefix at runtime.
- Output is a single JJB YAML document, written to stdout by default or to a
  path given by `-o/--output`.

  Producer and consumer run on the same host (libvirt `qemu:///session`), so the
  producer's domain is locally visible to the consumer.

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

Domains are named `ugt-<suite>-<test>-<YYYYMMDDTHHMMSSZ>` (UTC, fixed-width),
with a `-N` collision counter appended only when the same second is reused.
The fixed-width timestamp makes names lexicographically sortable, so the most
recent domain for a prefix is simply the greatest matching name.

The runner gains `--source-domain-prefix`: it lists domains, selects the latest
one matching `ugt-<suite>-<test>-<timestamp>`, and clones it. This lets consumer
jobs resolve the producer's domain without any out-of-band parameter passing.

## Error handling

- Any validation error (malformed schema, both/neither source, dangling
  `depends-on` reference, dependency cycle) results in a non-zero exit code
  and a clear error message. No partial output is written.

## Testing

`job-generator/tests/`:

- `test_schema.py` — validation cases: valid input, both/neither source,
  dangling reference, cycle detection, producer/consumer derivation.
- `test_generator.py` — generated structure: the `job-template`/`project`
  shape, the shared SCM and shell skeleton, and per-test instances
  (`args` with `--keep` only for producers, `--source-domain-prefix` for
  domain-sourced tests, and a `reverse` trigger on the producer for consumers).
- `test_cli.py` — end-to-end: input YAML file → generated JJB YAML.

`runner/tests/`:

- Tests asserting the UTC-timestamp domain naming and that
  `resolve_latest_domain` selects the newest matching domain (and raises when
  none match).

## Quality gates

Per `AGENTS.md`, run in both `job-generator/` and `runner/`:

```bash
uv run ruff format .
uv run ruff check .
uv run mypy .
uv run pytest tests/
```
