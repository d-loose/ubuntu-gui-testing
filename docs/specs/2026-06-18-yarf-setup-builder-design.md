# yarf setup builder — design

## Context

Generated Jenkins jobs invoke the runner, which spawns `yarf` as a bare
executable resolved from `PATH` (see `runner/ubuntu_gui_testing_runner/base.py`,
`_spawn_yarf`). `yarf` is a uv-managed Python project: it is set up from source
by cloning `https://github.com/canonical/yarf`, running `uv sync`, which
produces an entry point at `<clone>/.venv/bin/yarf`.

Jobs must provision `yarf` themselves, self-contained per build, without
installing it permanently on the agent.

## Goal

Add a builder to every generated job that clones and builds `yarf` into the job
workspace and makes it available on `PATH` for the runner invocation.

## Design

All jobs share a single JJB `job-template`, so the setup is added there once and
applies to every job.

### Configurable revision

The template declares a core-Jenkins string build parameter:

- `YARF_REF` — default `main`, description noting it selects the yarf git ref.

It is overridable from the Jenkins UI. Reverse-triggered builds (consumers) pass
no parameters and therefore use the default.

### Setup builder (first builder, identical for every job)

```bash
rm -rf "$WORKSPACE/yarf"
git clone --depth 1 --branch "$YARF_REF" https://github.com/canonical/yarf "$WORKSPACE/yarf"
cd "$WORKSPACE/yarf" && uv sync
```

- Cloned into `$WORKSPACE/yarf`, so it is self-contained in the job workspace
  and reclaimed by Jenkins workspace cleanup between runs. Nothing is installed
  system-wide.
- `--depth 1 --branch "$YARF_REF"` keeps the clone shallow. It supports branches
  and tags (not arbitrary commit SHAs); the default `main` and UI override cover
  the intended use.
- `uv sync` builds `$WORKSPACE/yarf/.venv/` including the `yarf` entry point at
  `$WORKSPACE/yarf/.venv/bin/yarf`.

### Runner builder PATH prepend

Jenkins freestyle shell steps are isolated processes, so `PATH` exported in one
builder does not carry to the next. The runner builder therefore prepends the
workspace venv to `PATH` itself as its first line:

```bash
export PATH="$WORKSPACE/yarf/.venv/bin:$PATH"
cd runner && uv run ubuntu-gui-testing-runner \
  --suite ../tests/{suite} \
  --test {test} \
  {args}
```

The bare `yarf` the runner spawns then resolves to the workspace venv.

## Generated output shape

The `job-template` gains a `parameters` block and a second builder; the
`project` instances are unchanged (`suite`, `test`, `args`, `triggers`).

## Testing

`job-generator/tests/test_generator.py`:

- The template declares the `YARF_REF` string parameter (default `main`).
- The template's first builder clones yarf and runs `uv sync`.
- The template's runner builder prepends `$WORKSPACE/yarf/.venv/bin` to `PATH`.

`test_cli.py` updated for the new output.

## Out of scope

- Caching the yarf checkout across builds.
- Pinning yarf by commit SHA.
- Any change to the runner itself (it already resolves `yarf` from `PATH`).
