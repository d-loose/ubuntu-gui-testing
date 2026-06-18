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

    job["scm"] = [{"git": {"url": REPO_URL, "branches": [BRANCH]}}]
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
