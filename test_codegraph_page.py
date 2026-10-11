import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import codegraph_page


def build_db(folder: Path, with_signature: bool = True) -> None:
    db = folder / ".codegraph" / "codegraph.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    signature = ", signature TEXT, docstring TEXT" if with_signature else ""
    conn.executescript(
        """
        CREATE TABLE files (
            path TEXT PRIMARY KEY,
            language TEXT,
            node_count INTEGER
        );
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY,
            kind TEXT,
            name TEXT,
            qualified_name TEXT,
            file_path TEXT,
            language TEXT,
            start_line INTEGER,
            end_line INTEGER
            %s
        );
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY,
            source TEXT,
            target TEXT,
            kind TEXT,
            line INTEGER
        );
        """ % signature
    )
    conn.execute("INSERT INTO files VALUES ('app/search.py', 'Python', 2)")
    conn.execute("INSERT INTO files VALUES ('.env', 'Unknown', 1)")
    if with_signature:
        conn.execute(
            "INSERT INTO nodes VALUES ('a', 'class', 'Search', 'Search', 'app/search.py', 'Python', 1, 40, ?, ?)",
            ("class Search", "检索入口"),
        )
        conn.execute(
            "INSERT INTO nodes VALUES ('b', 'function', 'run', 'Search.run', 'app/search.py', 'Python', 10, 20, ?, ?)",
            ("def run(self, api_key=sk-secret)", ""),
        )
        conn.execute(
            "INSERT INTO nodes VALUES ('c', 'function', 'load', 'load', '.env', 'Unknown', 1, 2, 'def load()', '')"
        )
        conn.execute(
            "INSERT INTO nodes VALUES ('d', 'function', 'only', 'only', 'extra.py', 'Python', 3, 4, '<script>', '')"
        )
    else:
        conn.execute("INSERT INTO nodes VALUES ('a', 'class', 'Search', 'Search', 'app/search.py', 'Python', 1, 40)")
    conn.execute("INSERT INTO edges VALUES (1, 'b', 'a', 'calls', 12)")
    conn.execute("INSERT INTO edges VALUES (2, 'c', 'a', 'calls', 1)")
    conn.commit()
    conn.close()


class CodeGraphPageTests(unittest.TestCase):
    def test_page_lists_symbols_relations_and_skips_secrets(self):
        tmp = Path(tempfile.mkdtemp())
        build_db(tmp)

        def hidden(path: str) -> bool:
            return Path(path).name == ".env"

        def redact(text: str) -> str:
            return text.replace("sk-secret", "***")

        page = codegraph_page.write_page(tmp, hidden, redact)
        text = page.read_text(encoding="utf-8")
        self.assertEqual(page, tmp / ".codegraph" / "代码地图.html")
        self.assertIn("app/search.py", text)
        self.assertIn("Search.run", text)
        self.assertIn("calls", text)
        self.assertIn("Search.run -&gt; Search", text)
        self.assertIn("extra.py", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertNotIn("<script>", text)
        self.assertNotIn(".env", text)
        self.assertNotIn("sk-secret", text)
        self.assertIn("***", text)
        self.assertIn("检索入口", text)
        self.assertIn("【代码地图结尾】文件 2 符号 3 关系 1", text)
        self.assertNotIn("def load", text)

    def test_missing_optional_columns_still_render(self):
        tmp = Path(tempfile.mkdtemp())
        build_db(tmp, with_signature=False)
        page = codegraph_page.write_page(tmp)
        text = page.read_text(encoding="utf-8")
        self.assertIn("L1-40  class  Search", text)
        self.assertIn("【代码地图结尾】文件 2 符号 1 关系 0", text)

    def test_missing_index_explains_what_to_do(self):
        tmp = Path(tempfile.mkdtemp())
        with self.assertRaises(codegraph_page.MapError) as caught:
            codegraph_page.write_page(tmp)
        self.assertIn("建立/更新代码索引", str(caught.exception))

    @unittest.skipIf(os.name == "nt", "Windows 会交给系统打开页面")
    def test_open_is_windows_only(self):
        with self.assertRaises(OSError):
            codegraph_page.open_local_html(Path("代码地图.html"))


if __name__ == "__main__":
    unittest.main()
