"""把 CodeGraph 索引写成一页本地 HTML，供浏览器阅读。

只读项目目录里的 .codegraph/codegraph.db，不联网，不改索引。
页面包含已索引的文件、符号和关系，不包含源码正文。
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


PAGE_NAME = "代码地图.html"
END_MARK = "【代码地图结尾】"


class MapError(Exception):
    pass


def page_path(project: Path) -> Path:
    return Path(project) / ".codegraph" / PAGE_NAME


def find_db(project: Path) -> Path | None:
    folder = Path(project) / ".codegraph"
    preferred = folder / "codegraph.db"
    if preferred.is_file():
        return preferred
    if not folder.is_dir():
        return None
    found = [item for item in folder.glob("*.db") if item.name != "lessons.db"]
    if len(found) == 1:
        return found[0]
    return None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = conn.execute("PRAGMA table_info(%s)" % table).fetchall()
    except sqlite3.Error as exc:
        raise MapError("无法读取代码索引：%s" % exc) from exc
    return {row[1] for row in rows}


def _norm(path: str) -> str:
    return str(path).replace("\\", "/")


def _cell(row: sqlite3.Row, name: str, default=""):
    if name not in row.keys():
        return default
    value = row[name]
    return default if value is None else value


def load_map(project: Path, hidden=None) -> dict:
    """读出索引快照。hidden(path) 为真的文件及其符号、关系不进入页面。"""
    hidden = hidden or (lambda _path: False)
    db = find_db(project)
    if db is None:
        raise MapError("还没有代码索引。先点“建立/更新代码索引”。")
    uri = "%s?mode=ro" % db.resolve().as_uri()
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error as exc:
        raise MapError("无法读取代码索引：%s" % exc) from exc
    conn.row_factory = sqlite3.Row
    try:
        file_cols = _columns(conn, "files")
        node_cols = _columns(conn, "nodes")
        needed = {"kind", "name", "qualified_name", "file_path", "start_line", "end_line"}
        if "path" not in file_cols or not needed <= node_cols:
            raise MapError("代码索引格式无法识别。")
        file_sql = "SELECT %s FROM files ORDER BY path" % ", ".join(
            name for name in ("path", "language", "node_count") if name in file_cols
        )
        node_wanted = (
            "kind", "name", "qualified_name", "file_path", "language",
            "start_line", "end_line", "signature", "docstring",
        )
        node_sql = "SELECT %s FROM nodes ORDER BY file_path, start_line, name" % ", ".join(
            name for name in node_wanted if name in node_cols
        )
        try:
            file_rows = list(conn.execute(file_sql))
            node_rows = list(conn.execute(node_sql))
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower():
                raise MapError("代码索引正在写入，稍后再点“打开代码地图”。") from exc
            raise MapError("无法读取代码索引：%s" % exc) from exc
        edge_rows = []
        edge_cols = _columns(conn, "edges")
        if {"source", "target", "kind"} <= edge_cols and "id" in node_cols:
            edge_sql = (
                "SELECT e.kind AS kind, s.qualified_name AS source_name, s.file_path AS source_file, "
                "t.qualified_name AS target_name, t.file_path AS target_file, e.line AS line "
                "FROM edges e JOIN nodes s ON s.id = e.source JOIN nodes t ON t.id = e.target "
                "ORDER BY s.file_path, e.line, e.kind"
            )
            try:
                edge_rows = list(conn.execute(edge_sql))
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower():
                    raise MapError("代码索引正在写入，稍后再点“打开代码地图”。") from exc
                raise MapError("无法读取代码索引：%s" % exc) from exc
    finally:
        conn.close()

    kept_files = []
    languages: dict[str, str] = {}
    for row in file_rows:
        path = _norm(_cell(row, "path"))
        if not path or hidden(path):
            continue
        languages[path] = str(_cell(row, "language", "未知"))
        kept_files.append({"path": path, "language": languages[path]})
    known = {item["path"] for item in kept_files}
    for row in node_rows:
        path = _norm(_cell(row, "file_path"))
        if not path or path in known or hidden(path):
            continue
        language = str(_cell(row, "language", languages.get(path, "未知")))
        kept_files.append({"path": path, "language": language})
        known.add(path)
    kept_files.sort(key=lambda item: item["path"])
    kept_paths = {item["path"] for item in kept_files}
    symbols = []
    for row in node_rows:
        path = _norm(_cell(row, "file_path"))
        if path not in kept_paths:
            continue
        symbols.append({
            "kind": str(_cell(row, "kind")),
            "name": str(_cell(row, "name")),
            "qualified_name": str(_cell(row, "qualified_name") or _cell(row, "name")),
            "file_path": path,
            "start_line": int(_cell(row, "start_line", 0) or 0),
            "end_line": int(_cell(row, "end_line", 0) or 0),
            "signature": str(_cell(row, "signature")),
            "docstring": str(_cell(row, "docstring")),
        })
    relations = []
    for row in edge_rows:
        source_file = _norm(_cell(row, "source_file"))
        target_file = _norm(_cell(row, "target_file"))
        if source_file not in kept_paths or target_file not in kept_paths:
            continue
        line = _cell(row, "line", "")
        relations.append({
            "kind": str(_cell(row, "kind")),
            "source": str(_cell(row, "source_name")),
            "target": str(_cell(row, "target_name")),
            "file_path": source_file,
            "line": "" if line == "" else str(line),
        })
    counts: dict[str, int] = {}
    for item in symbols:
        counts[item["file_path"]] = counts.get(item["file_path"], 0) + 1
    for item in kept_files:
        item["symbols"] = counts.get(item["path"], 0)
    return {"files": kept_files, "symbols": symbols, "relations": relations}


def _esc(text: object) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _clean(text: str, redact_text, limit: int) -> str:
    value = " ".join(str(text).split())
    if redact_text is not None and value:
        value = redact_text(value)
    if len(value) > limit:
        value = value[:limit] + "…"
    return value


def write_page(project: Path, hidden=None, redact_text=None) -> Path:
    data = load_map(project, hidden)
    dest = page_path(project)
    dest.parent.mkdir(parents=True, exist_ok=True)
    files = data["files"]
    symbols = data["symbols"]
    relations = data["relations"]
    by_file: dict[str, list[dict]] = {}
    for item in symbols:
        by_file.setdefault(item["file_path"], []).append(item)
    marker = "%s文件 %d 符号 %d 关系 %d" % (END_MARK, len(files), len(symbols), len(relations))
    with dest.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(
            "<!doctype html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<title>代码地图</title></head><body>\n"
        )
        handle.write("<h1>代码地图</h1>\n")
        handle.write("<p>项目目录：%s</p>\n" % _esc(Path(project).resolve()))
        handle.write(
            "<p>这一页是 CodeGraph 索引里的全部文件、符号和关系，不含源码正文。"
            "敏感文件名已去掉。请在 Edge 中阅读本页，并核对页末标记。</p>\n"
        )
        handle.write("<h2>统计</h2>\n<pre>")
        handle.write("文件 %d\n符号 %d\n关系 %d\n</pre>\n" % (len(files), len(symbols), len(relations)))
        handle.write("<h2>文件</h2>\n<pre>")
        if not files:
            handle.write("（索引里没有可显示的文件）\n")
        for item in files:
            handle.write("%s  %s  符号 %d\n" % (_esc(item["path"]), _esc(item["language"]), item["symbols"]))
        handle.write("</pre>\n<h2>符号</h2>\n<pre>")
        if not symbols:
            handle.write("（没有符号）\n")
        for item in files:
            group = by_file.get(item["path"])
            if not group:
                continue
            handle.write("\n%s\n" % _esc(item["path"]))
            for symbol in group:
                signature = _clean(symbol["signature"], redact_text, 300)
                note = _clean(symbol["docstring"], redact_text, 180)
                extra = ""
                if signature:
                    extra += "  " + _esc(signature)
                if note:
                    extra += "  " + _esc(note)
                handle.write(
                    "  L%s-%s  %s  %s%s\n" % (
                        symbol["start_line"], symbol["end_line"],
                        _esc(symbol["kind"]), _esc(symbol["qualified_name"]), extra,
                    )
                )
        handle.write("</pre>\n<h2>关系</h2>\n<pre>")
        handle.write("种类沿用索引中的英文名，例如 calls、imports、extends、contains。\n")
        if not relations:
            handle.write("（没有关系）\n")
        for item in relations:
            place = item["file_path"]
            if item["line"]:
                place = "%s:%s" % (place, item["line"])
            handle.write(
                "%s  %s -&gt; %s  %s\n" % (
                    _esc(item["kind"]), _esc(item["source"]), _esc(item["target"]), _esc(place),
                )
            )
        handle.write("</pre>\n<p>%s</p>\n</body></html>\n" % _esc(marker))
    return dest


def open_local_html(path: Path) -> None:
    if os.name != "nt":
        raise OSError("只能在 Windows 上打开本地页面")
    os.startfile(os.fspath(path))  # type: ignore[attr-defined]
