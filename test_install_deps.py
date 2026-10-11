import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

import install_deps


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_package(directory: Path, files: dict[str, bytes], manifest_names: list[str] | None = None) -> tuple[Path, str]:
    listed = manifest_names if manifest_names is not None else list(files)
    lines = []
    for name in listed:
        lines.append("%s  %s" % (sha256(files[name]), name))
    payload = dict(files)
    payload[install_deps.MANIFEST_NAME] = ("\n".join(lines) + "\n").encode("utf-8")
    package = directory / install_deps.PACKAGE_NAME
    with zipfile.ZipFile(package, "w") as archive:
        for name, data in payload.items():
            archive.writestr(name, data)
    digest = sha256(package.read_bytes())
    sums = "%s  %s\n" % (digest, install_deps.PACKAGE_NAME)
    return package, sums


class InstallDepsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.tool = self.tmp / "app"
        self.tool.mkdir()

    def test_bad_digest_stops_before_anything_runs(self):
        package, _sums = make_package(self.tmp, {"markitdown-0.1.7-py3-none-any.whl": b"wheel"})
        calls = []

        with self.assertRaises(install_deps.InstallError) as caught:
            install_deps.install(
                self.tool, package, "0" * 64 + "  " + install_deps.PACKAGE_NAME + "\n",
                "python", (3, 11), lambda argv: calls.append(argv) or (0, ""),
            )
        self.assertIn("校验", str(caught.exception))
        self.assertEqual(calls, [])
        self.assertFalse((self.tool / "upload" / "_deps_tmp").exists())

    def test_file_outside_manifest_stops_install(self):
        files = {
            "markitdown-0.1.7-py3-none-any.whl": b"wheel",
            "extra.txt": b"nope",
        }
        package, sums = make_package(
            self.tmp, files, manifest_names=["markitdown-0.1.7-py3-none-any.whl"],
        )
        calls = []

        with self.assertRaises(install_deps.InstallError) as caught:
            install_deps.install(
                self.tool, package, sums, "python", (3, 11),
                lambda argv: calls.append(argv) or (0, ""),
            )
        self.assertIn("清单外", str(caught.exception))
        self.assertEqual(calls, [])
        self.assertFalse((self.tool / "vendor").exists())

    def test_pip_uses_local_files_and_program_dir(self):
        files = {"markitdown-0.1.7-py3-none-any.whl": b"wheel"}
        package, sums = make_package(self.tmp, files)
        calls = []

        def runner(argv):
            calls.append(list(argv))
            if argv[1:3] == ["-m", "pip"] and "--version" in argv:
                return 0, "pip 24"
            return 0, "ok"

        message = install_deps.install(
            self.tool, package, sums, r"C:\Py\python.exe", (3, 11), runner,
            self_check=lambda _target: None,
        )
        pip = next(argv for argv in calls if "install" in argv)
        self.assertIn("--no-index", pip)
        self.assertNotIn("--index-url", pip)
        target = Path(pip[pip.index("--target") + 1])
        self.assertEqual(target, (self.tool / "vendor" / "py311").resolve())
        self.assertIn(install_deps.MARKITDOWN_SPEC, pip)
        self.assertIn("已安装", message)
        self.assertFalse((self.tool / "upload" / "_deps_tmp").exists())
        self.assertNotIn("D:\\", str(target))

    def test_missing_pip_bootstraps_offline(self):
        files = {"markitdown-0.1.7-py3-none-any.whl": b"wheel"}
        package, sums = make_package(self.tmp, files)
        calls = []

        def runner(argv):
            calls.append(list(argv))
            if "--version" in argv:
                return 1, "No module named pip"
            return 0, "ok"

        install_deps.install(
            self.tool, package, sums, "python", (3, 12), runner,
            self_check=lambda _target: None,
        )
        self.assertEqual(calls[0][-2:], ["pip", "--version"])
        self.assertIn("ensurepip", calls[1])
        self.assertIn("--default-pip", calls[1])

    def test_rejects_archive_path_escape(self):
        package = self.tmp / install_deps.PACKAGE_NAME
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("../outside.txt", b"x")
        digest = sha256(package.read_bytes())
        with self.assertRaises(install_deps.InstallError) as caught:
            install_deps.install(
                self.tool, package, "%s  %s\n" % (digest, install_deps.PACKAGE_NAME),
                "python", (3, 11), lambda argv: (0, ""),
            )
        self.assertIn("不安全", str(caught.exception))
        self.assertFalse((self.tmp / "outside.txt").exists())
