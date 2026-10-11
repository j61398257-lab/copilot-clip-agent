"""把文档组件装进程序目录。只用本地文件，不连接 PyPI。"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

PACKAGE_NAME = "docs-deps-win64.zip"
MANIFEST_NAME = "manifest.txt"
MARKITDOWN_SPEC = "markitdown[docx,pdf,pptx,xlsx,xls,outlook]==0.1.7"
TEMP_DIRNAME = "_deps_tmp"


class InstallError(Exception):
    pass


def parse_sums(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        digest, sep, name = line.partition(" ")
        name = name.strip().lstrip("*")
        if not sep or len(digest) != 64 or not name:
            continue
        if any(char not in "0123456789abcdefABCDEF" for char in digest):
            continue
        found[name] = digest.lower()
    return found


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def vendor_dirname(version: tuple[int, int]) -> str:
    return "py%d%d" % (version[0], version[1])


def pip_argv(python: str, links: Path, target: Path) -> list[str]:
    return [
        python, "-m", "pip", "install",
        "--no-index",
        "--find-links", str(links),
        "--target", str(target),
        "--ignore-installed",
        MARKITDOWN_SPEC,
    ]


def find_package(tool_dir: Path) -> Path | None:
    for folder in (tool_dir, Path.home() / "Downloads"):
        candidate = folder / PACKAGE_NAME
        if candidate.is_file():
            return candidate
    return None


def extract_safe(package: Path, dest: Path) -> None:
    root = dest.resolve()
    with zipfile.ZipFile(package) as archive:
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            if not name or name.startswith("/") or ".." in name.split("/"):
                raise InstallError("压缩包路径不安全。")
            target = (root / name).resolve()
            if target != root and root not in target.parents:
                raise InstallError("压缩包路径不安全。")
        archive.extractall(root)


def check_extracted(directory: Path, manifest: dict[str, str]) -> str | None:
    found: dict[str, Path] = {}
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(directory).as_posix()
        if rel == MANIFEST_NAME:
            continue
        found[rel] = path
    extra = sorted(set(found) - set(manifest))
    if extra:
        return "清单外的文件：%s" % ", ".join(extra)
    missing = sorted(set(manifest) - set(found))
    if missing:
        return "缺少文件：%s" % ", ".join(missing)
    for name, digest in manifest.items():
        if file_sha256(found[name]) != digest:
            return "文件校验不一致：%s" % name
    return None


def _write_zip(path: Path, members: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in members.items():
            archive.writestr(name, text.encode("utf-8"))


def sample_office_files(directory: Path) -> tuple[Path, Path]:
    docx = directory / "自检.docx"
    _write_zip(docx, {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>"
        ),
        "word/document.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>自检文档</w:t></w:r></w:p></w:body>"
            "</w:document>"
        ),
    })
    xlsx = directory / "自检.xlsx"
    _write_zip(xlsx, {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/>'
            "</Relationships>"
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>'
            "</workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/>'
            "</Relationships>"
        ),
        "xl/styles.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
            '<borders count="1"><border/></borders>'
            '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
            '<cellXfs count="1"><xf/></cellXfs>'
            "</styleSheet>"
        ),
        "xl/worksheets/sheet1.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>自检表格</t></is></c></row></sheetData>'
            "</worksheet>"
        ),
    })
    return docx, xlsx


SELF_CHECK = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from markitdown import MarkItDown\n"
    "tool = MarkItDown(enable_plugins=False)\n"
    "for path in sys.argv[2:]:\n"
    "    text = tool.convert_local(path).markdown or ''\n"
    "    if '自检' not in text:\n"
    "        raise SystemExit('转换结果里没有预期文字：' + path)\n"
    "print('自检通过')\n"
)


def run_self_check(target: Path, python: str) -> None:
    with tempfile.TemporaryDirectory() as folder:
        docx, xlsx = sample_office_files(Path(folder))
        proc = subprocess.run(
            [python, "-I", "-X", "utf8", "-c", SELF_CHECK, str(target), str(docx), str(xlsx)],
            capture_output=True,
            timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    output = (proc.stdout + b"\n" + proc.stderr).decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        raise InstallError("自检失败：%s" % (output or "没有输出"))


def _append_log(tool_dir: Path, text: str) -> None:
    try:
        folder = tool_dir / "logs"
        folder.mkdir(exist_ok=True)
        stamp = datetime.now().isoformat(timespec="seconds")
        with (folder / "install.log").open("a", encoding="utf-8") as handle:
            handle.write("%s %s\n" % (stamp, text))
    except OSError:
        pass


def install(
    tool_dir: Path,
    package: Path,
    sums_text: str,
    python: str,
    version: tuple[int, int],
    runner,
    self_check=None,
) -> str:
    expected = parse_sums(sums_text).get(PACKAGE_NAME)
    if not expected:
        raise InstallError("校验清单里没有 %s。" % PACKAGE_NAME)
    if file_sha256(package) != expected:
        raise InstallError("校验不一致，已停止安装。")
    if version < (3, 10):
        raise InstallError("需要 Python 3.10 或更高版本。")
    tmp = tool_dir / "upload" / TEMP_DIRNAME
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        extract_safe(package, tmp)
        manifest_path = tmp / MANIFEST_NAME
        if not manifest_path.is_file():
            raise InstallError("压缩包里没有清单。")
        problem = check_extracted(tmp, parse_sums(manifest_path.read_text(encoding="utf-8")))
        if problem:
            raise InstallError(problem)
        code, output = runner([python, "-m", "pip", "--version"])
        if code != 0:
            code, output = runner([python, "-m", "ensurepip", "--default-pip"])
            if code != 0:
                raise InstallError("无法准备 pip：%s" % output.strip())
        target = (tool_dir / "vendor" / vendor_dirname(version)).resolve()
        argv = pip_argv(python, tmp.resolve(), target)
        code, output = runner(argv)
        if code != 0:
            raise InstallError("安装失败：%s" % output.strip()[-800:])
        if self_check is None:
            run_self_check(target, python)
        else:
            self_check(target)
        _append_log(tool_dir, "已安装 %s -> %s" % (file_sha256(package), target))
        return "文档组件已安装到 %s" % target
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def python_exe() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").is_file():
        return str(exe.with_name("python.exe"))
    return str(exe)


def run_command(argv: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        argv,
        capture_output=True,
        timeout=900,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    text = (proc.stdout + b"\n" + proc.stderr).decode("utf-8", errors="replace")
    return proc.returncode, text


def main(argv: list[str] | None = None) -> int:
    del argv
    tool = Path(__file__).resolve().parent
    sums_path = tool / "deps" / "SHA256SUMS"
    if not sums_path.is_file():
        print("找不到 deps\\SHA256SUMS。")
        return 1
    package = find_package(tool)
    if package is None:
        print("找不到 %s。请把它放在程序目录或“下载”文件夹。" % PACKAGE_NAME)
        return 1
    version = sys.version_info[:2]
    print("正在安装文档组件（Python %d.%d），只使用本地文件。" % version)
    try:
        print(install(tool, package, sums_path.read_text(encoding="utf-8"), python_exe(), version, run_command))
    except InstallError as exc:
        print(exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
