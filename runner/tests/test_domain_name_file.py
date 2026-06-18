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
