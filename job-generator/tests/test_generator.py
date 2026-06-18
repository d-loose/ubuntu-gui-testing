from pathlib import Path

from job_generator.generator import generate_jobs
from job_generator.schema import Config, load_config


def _config(tmp_path: Path, content: str) -> Config:
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
    assert "triggers" not in producer
    assert "publishers" not in producer


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
    assert consumer["triggers"] == [
        {
            "reverse": {
                "jobs": "ugt-desktop-installer-resolute.entire-disk",
                "result": "success",
            }
        }
    ]
    shell = consumer["builders"][0]["shell"]
    assert "--suite ../tests/firefox-example" in shell
    assert "--test firefox-example-basic" in shell
    assert "--source-domain-prefix ugt-desktop-installer-resolute.entire-disk" in shell
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
    assert "triggers" not in job
