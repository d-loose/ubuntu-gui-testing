from __future__ import annotations

from typing import Any

from job_generator.schema import Config, GeneratorError, Test

REPO_URL = "https://github.com/canonical/ubuntu-gui-testing/"
BRANCH = "main"
ISO_DIR = "/isos"


def generate_jobs(config: Config) -> list[dict[str, Any]]:
    return [{"job": _build_job(config, test)} for test in config.tests]


def _build_job(config: Config, test: Test) -> dict[str, Any]:
    is_producer = config.is_producer(test)
    producer = config.producer_of(test)
    job: dict[str, Any] = {"name": test.job_name}

    if producer is not None:
        job["triggers"] = [
            {
                "reverse": {
                    "jobs": producer.job_name,
                    "result": "success",
                }
            }
        ]

    job["scm"] = [{"git": {"url": REPO_URL, "branches": [BRANCH]}}]
    job["builders"] = [{"shell": _build_shell(test, is_producer, producer)}]

    return job


def _build_shell(test: Test, is_producer: bool, producer: Test | None) -> str:
    lines = [
        "cd runner && uv run ubuntu-gui-testing-runner \\",
        f"  --suite ../tests/{test.suite} \\",
        f"  --test {test.name} \\",
    ]
    if test.iso is not None:
        source = f"  --iso {ISO_DIR}/{test.iso}"
    elif producer is not None:
        source = f"  --source-domain-prefix {producer.job_name}"
    else:
        raise GeneratorError(
            f"Test '{test.key}' has neither 'iso' nor a resolvable producer"
        )
    if is_producer:
        source += " \\"
        lines.append(source)
        lines.append("  --keep")
    else:
        lines.append(source)
    return "\n".join(lines) + "\n"
