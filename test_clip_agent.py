import io
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import clip_agent
import install_deps


SESSION = "K7Q2"


class MemoryAudit:
    def __init__(self):
        self.records = []

    def write(self, record):
        self.records.append(dict(record))
        return record


def make_runtime(root, mode="readonly", session=SESSION, max_chars=8000, max_batch=5, markers=None):
    return clip_agent.Runtime(
        mode=mode,
        cwd=Path(root),
        roots=[Path(root).resolve()],
        session=session,
        max_chars=max_chars,
        max_batch=max_batch,
        timeout=5,
        markers=list(markers or []),
        input_method="pipe",
        store=clip_agent.ResultStore(),
        audit=MemoryAudit(),
        user="tester",
        host="test",
    )


def fake_executor(directive, runtime):
    return clip_agent.ExecResult(0, "OUT:" + directive.body, 0.01)


def make_id(body):
    total = sum(int(body[index]) * clip_agent.ID_WEIGHTS[index] for index in range(17))
    return body + clip_agent.ID_CODES[total % 11]


class ParseTests(unittest.TestCase):
    def test_session_code_alphabet(self):
        for _ in range(30):
            code = clip_agent.new_session_code()
            self.assertEqual(len(code), 4)
            self.assertTrue(set(code) <= set(clip_agent.CODE_ALPHABET))

    def test_code_fence_zero_width_fullwidth_and_nbsp(self):
        text = (
            "```powershell\n"
            "TO\u200bOL[K7Q2]\u00a0： Get-Location\n"
            "```\n"
        )
        directives, error = clip_agent.parse_batch(text, SESSION, 5)
        self.assertIsNone(error)
        self.assertEqual(len(directives), 1)
        self.assertEqual(directives[0].kind, "TOOL")
        self.assertEqual(directives[0].body, "Get-Location")

    def test_bidi_rejects_batch(self):
        directives, error = clip_agent.parse_batch("TOOL[K7Q2]: Get-Location\u202e", SESSION, 5)
        self.assertIsNone(directives)
        self.assertIn("双向", error)

    def test_line_too_long(self):
        line = "TOOL[K7Q2]: " + ("A" * 5000)
        directives, error = clip_agent.parse_batch(line, SESSION, 5)
        self.assertIsNone(directives)
        self.assertIn("单行", error)

    def test_too_many_directives(self):
        lines = "\n".join("TOOL[K7Q2]: Get-Location" for _ in range(6))
        directives, error = clip_agent.parse_batch(lines, SESSION, 5)
        self.assertIsNone(directives)
        self.assertIn("上限", error)

    def test_wrong_session_and_missing_code(self):
        runtime = make_runtime(tempfile.gettempdir())
        text = clip_agent.handle_text("TOOL[ZZZZ]: Get-Location", runtime, fake_executor, lambda *_: True)
        self.assertIn("[REJECTED]", text)
        self.assertNotIn(SESSION, text)
        text = clip_agent.handle_text("TOOL: Get-Location", runtime, fake_executor, lambda *_: True)
        self.assertIn("[REJECTED]", text)
        self.assertNotIn(SESSION, text)

    def test_ignores_prose(self):
        runtime = make_runtime(tempfile.gettempdir())
        text = clip_agent.handle_text("这不是指令\n请看下面", runtime, fake_executor, lambda *_: True)
        self.assertIsNone(text)
        self.assertEqual(runtime.audit.records, [])

    def test_reads_directives_the_way_copilot_formats_them(self):
        text = "\n".join([
            "下面先看目录和说明文件：",
            "READ_FILE 会返回带行号的内容。",
            "```powershell",
            "1. TOOL[K7Q2]: Get-ChildItem -Name",
            "- `READ_FILE[K7Q2]: README.md#L1-20`",
            "2、TOOL[k7q2]: git status --short",
            "> TOOL [K7Q2]: Get-Location",
            "```",
            "看完结果再决定下一步。",
        ])
        directives, error = clip_agent.parse_batch(text, SESSION, 5)
        self.assertIsNone(error)
        self.assertEqual(
            [(item.kind, item.body) for item in directives],
            [
                ("TOOL", "Get-ChildItem -Name"),
                ("READ_FILE", "README.md#L1-20"),
                ("TOOL", "git status --short"),
                ("TOOL", "Get-Location"),
            ],
        )

    def test_python_block_keeps_code_as_written(self):
        text = "\n".join([
            "```text",
            "PYTHON[K7Q2]:",
            "import csv",
            "",
            "for row in csv.reader(open('a.csv', encoding='utf-8')):",
            "    print('行：', row)",
            "END_PYTHON",
            "TOOL[K7Q2]: Get-Location",
            "```",
        ])
        directives, error = clip_agent.parse_batch(text, SESSION, 5)
        self.assertIsNone(error)
        self.assertEqual([item.kind for item in directives], ["PYTHON", "TOOL"])
        self.assertEqual(
            directives[0].body,
            "import csv\n\nfor row in csv.reader(open('a.csv', encoding='utf-8')):\n    print('行：', row)",
        )

    def test_python_block_needs_end_and_right_code(self):
        directives, error = clip_agent.parse_batch(
            "PYTHON[K7Q2]:\nprint(1)\nTOOL[K7Q2]: Get-Location", SESSION, 5,
        )
        self.assertIsNone(directives)
        self.assertIn("END_PYTHON", error)
        directives, error = clip_agent.parse_batch("PYTHON[ZZZZ]:\nprint(1)\nEND_PYTHON", SESSION, 5)
        self.assertIsNone(directives)
        self.assertIn("会话码", error)

    def test_read_spec_range(self):
        spec = clip_agent.parse_read_spec(r"result:3#L10-20")
        self.assertEqual(spec["type"], "result")
        self.assertEqual(spec["n"], 3)
        self.assertEqual((spec["start"], spec["end"]), (10, 20))


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "work"
        self.root.mkdir()
        self.outside = self.tmp / "out"
        self.outside.mkdir()
        (self.root / "a.txt").write_text("hello", encoding="utf-8")
        (self.outside / "secret.txt").write_text("x", encoding="utf-8")
        (self.root / "sub").mkdir()

    def classify(self, command, mode="readonly"):
        return clip_agent.classify_tool(command, mode, self.root, [self.root.resolve()])

    def test_allows_readonly_pipeline_and_git(self):
        decision, _ = self.classify("Get-ChildItem -Name | Select-Object -First 1")
        self.assertEqual(decision, "allow")
        decision, _ = self.classify('Get-Content "a.txt" -Encoding UTF8')
        self.assertEqual(decision, "allow")
        decision, _ = self.classify("Format-Table Name")
        self.assertEqual(decision, "allow")
        decision, _ = self.classify("git status")
        self.assertEqual(decision, "allow")
        decision, _ = self.classify("git log --pretty=oneline -n 5")
        self.assertEqual(decision, "allow")
        decision, _ = self.classify("cd sub")
        self.assertEqual(decision, "allow")

    def test_blocks_escape_sensitive_and_metacharacters(self):
        cases = [
            r"Get-Content ..\out\secret.txt",
            "Get-Content $env:USERNAME",
            "Get-Content env:PATH",
            "Get-Content .env",
            "Get-Content id_rsa",
            "Get-ChildItem ; Remove-Item a.txt",
            "git push",
            "git -c core.pager=more status",
            "git log --output=out.txt",
            "Remove-Item -Recurse .",
            "cmd /c format C:",
            "Format-Table ; diskpart",
            'Get-Content "a.txt',
        ]
        for command in cases:
            decision, reason = self.classify(command)
            self.assertEqual(decision, "block", command + " -> " + reason)

    def test_cd_outside_blocked_in_both_modes(self):
        for mode in ("readonly", "tiered"):
            decision, _ = self.classify("cd ..", mode)
            self.assertEqual(decision, "block", mode)

    def test_tiered_confirms_write_but_blocks_high_risk(self):
        decision, _ = self.classify("Set-Content a.txt hello", "tiered")
        self.assertEqual(decision, "confirm")
        decision, _ = self.classify("Remove-Item -Recurse a.txt", "tiered")
        self.assertEqual(decision, "block")
        decision, _ = clip_agent.classify_read(
            str(self.outside / "secret.txt"), "tiered", self.root, [self.root.resolve()],
        )
        self.assertEqual(decision, "confirm")
        decision, _ = clip_agent.classify_read(".env", "tiered", self.root, [self.root.resolve()])
        self.assertEqual(decision, "block")

    def test_roots_reject_drive_home_and_windows(self):
        drive = Path(os.environ["SystemDrive"] + "\\")
        self.assertIn("盘符", clip_agent.root_problem(drive))
        self.assertIn("用户主目录", clip_agent.root_problem(Path.home()))
        self.assertIn("Windows", clip_agent.root_problem(Path(os.environ["SystemRoot"])))
        self.assertIn("Windows", clip_agent.root_problem(Path(os.environ["SystemRoot"]) / "System32"))
        self.assertIsNone(clip_agent.root_problem(self.root))


class BatchAndConfirmTests(unittest.TestCase):
    def test_readonly_continues_after_block(self):
        runtime = make_runtime(tempfile.gettempdir())
        called = []

        def executor(directive, runtime):
            called.append(directive.body)
            return clip_agent.ExecResult(0, "ok", 0.01)

        text = "\n".join([
            "TOOL[K7Q2]: Get-Location",
            "TOOL[K7Q2]: Remove-Item -Recurse .",
            "TOOL[K7Q2]: Get-Location",
        ])
        rendered = clip_agent.handle_text(text, runtime, executor, lambda *_: (_ for _ in ()).throw(AssertionError("no confirm")))
        self.assertEqual(called, ["Get-Location", "Get-Location"])
        self.assertIn("BLOCKED", rendered)
        self.assertEqual(rendered.count("[RESULT #"), 3)

    def test_tiered_denial_stops_batch_and_ignores_pasted_y(self):
        runtime = make_runtime(tempfile.gettempdir(), mode="tiered")
        called = []

        def executor(directive, runtime):
            called.append(directive.body)
            return clip_agent.ExecResult(0, "ok", 0.01)

        text = "\n".join([
            "TOOL[K7Q2]: Set-Content a.txt 1",
            "y",
            "TOOL[K7Q2]: Get-Location",
        ])
        rendered = clip_agent.handle_text(text, runtime, executor, lambda *_: False)
        self.assertEqual(called, [])
        self.assertIn("未得到确认", rendered)
        self.assertIn("SKIP", rendered.upper())

    def test_tiered_start_accepts_either_case(self):
        self.assertTrue(clip_agent.accepted_start("YES\n"))
        self.assertTrue(clip_agent.accepted_start("yes\n"))
        self.assertTrue(clip_agent.accepted_start(" Yes \n"))
        self.assertFalse(clip_agent.accepted_start("y\n"))
        self.assertFalse(clip_agent.accepted_start("\n"))

    def test_confirm_flushes_before_read(self):
        seen = {}

        def flush():
            seen["flush"] = True

        def show(command, reason):
            seen["show"] = (command, reason)

        self.assertTrue(clip_agent.ask_confirm("cmd", "原因", flush, show, lambda: "Y\n"))
        self.assertTrue(seen["flush"])
        self.assertFalse(clip_agent.ask_confirm("cmd", "原因", lambda: None, lambda *_: None, lambda: "\n"))
        self.assertFalse(clip_agent.ask_confirm("cmd", "原因", lambda: None, lambda *_: None, lambda: "yes\n"))

    def test_collect_pending_and_pipe_groups(self):
        pending_reads = ["one\n", "two\n", "three\n"]
        state = {"n": 0}

        def read_line():
            return pending_reads.pop(0)

        def pending():
            state["n"] += 1
            return state["n"] == 1

        rows = clip_agent.collect_pending(read_line, pending, lambda _seconds: None)
        self.assertEqual(rows, ["one", "two"])
        self.assertEqual(
            clip_agent.group_pipe_lines(["a", "b", "", "c", ""]),
            [["a", "b"], ["c"]],
        )

    def test_wait_for_input_uses_injected_clock(self):
        clock = {"t": 0.0}

        def now():
            return clock["t"]

        def pause(seconds):
            clock["t"] += seconds

        self.assertFalse(clip_agent.wait_for_input(lambda: False, pause, now, 1))
        clock["t"] = 0
        self.assertTrue(clip_agent.wait_for_input(lambda: True, pause, now, 1))


class ConsoleTests(unittest.TestCase):
    def feed(self, rows, runtime):
        written = []
        said = io.StringIO()
        with redirect_stderr(said):
            action = clip_agent.consume_rows(
                rows, runtime, fake_executor, lambda *_: True,
                lambda text: written.append(text) or 1, False, None, 120,
            )
        return action, written, said.getvalue()

    def test_non_directive_input_says_what_to_paste(self):
        runtime = make_runtime(tempfile.gettempdir())
        _, written, said = self.feed(["python clip_agent.py --verify-audit logs\\a.jsonl"], runtime)
        self.assertEqual(written, [])
        self.assertIn("TOOL[K7Q2]:", said)
        self.assertIn("exit", said)

    def test_pasted_brief_is_not_run_and_clipboard_untouched(self):
        runtime = make_runtime(tempfile.gettempdir())
        brief = clip_agent.build_copilot_brief(
            SESSION, "readonly", runtime.cwd, runtime.roots, 5, 60, 8000, [],
        )
        _, written, said = self.feed(brief.splitlines(), runtime)
        self.assertEqual(written, [])
        self.assertEqual(runtime.audit.records, [])
        self.assertIn("Copilot", said)

    def test_brief_word_copies_brief_again(self):
        runtime = make_runtime(tempfile.gettempdir())
        runtime.brief = "# 本会话执行说明\n会话码：`K7Q2`"
        action, written, _ = self.feed(["说明"], runtime)
        self.assertIsNone(action)
        self.assertEqual(written, [runtime.brief])
        self.assertEqual(runtime.audit.records, [])

    def test_wrong_session_points_to_brief_word(self):
        runtime = make_runtime(tempfile.gettempdir())
        _, written, said = self.feed(["TOOL[ZZZZ]: Get-Location"], runtime)
        self.assertIn("[REJECTED]", written[0])
        self.assertIn("说明", said)


class PythonBlockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def block(self, code):
        return "PYTHON[K7Q2]:\n%s\nEND_PYTHON" % code

    def test_readonly_never_runs_python(self):
        runtime = make_runtime(self.root)
        asked = []
        rendered = clip_agent.handle_text(
            self.block("print(1)"), runtime, fake_executor, lambda *args: asked.append(args) or True,
        )
        self.assertEqual(asked, [])
        self.assertIn("BLOCKED PYTHON", rendered)
        self.assertIn("只读档", rendered)
        self.assertEqual(runtime.audit.records[-1]["decision"], "block")

    def test_tiered_shows_whole_code_before_running(self):
        runtime = make_runtime(self.root, mode="tiered")
        code = "import csv\nprint('合计', 3)"
        asked = []
        rendered = clip_agent.handle_text(
            self.block(code), runtime, fake_executor,
            lambda command, reason: asked.append(command) or True,
        )
        self.assertEqual(len(asked), 1)
        self.assertIn(code, asked[0])
        self.assertIn("[RESULT #1] PYTHON 2 行", rendered)
        self.assertEqual(runtime.audit.records[-1]["decision"], "allow")
        self.assertIn("print('合计', 3)", runtime.audit.records[-1]["directive"])

    def test_risky_code_is_blocked_without_asking(self):
        risky = [
            "import subprocess\nsubprocess.run(['whoami'])",
            "import os\nos.system('dir')",
            "import urllib.request\nurllib.request.urlopen('http://example.com')",
            "import pandas as pd\npd.read_csv('https://example.com/a.csv')",
            "import shutil\nshutil.rmtree('data')",
            "from pathlib import Path\nPath('a.txt').unlink()",
            "eval('1+1')",
            "__import__('os')",
            "import ctypes",
            "import pandas as pd\npd.read_clipboard()",
            "print(open('.env').read())",
        ]
        for code in risky:
            with self.subTest(code=code):
                decision, reason = clip_agent.classify_python(code, "tiered")
                self.assertEqual(decision, "block", reason)

    def test_everyday_analysis_code_goes_to_confirmation(self):
        fine = [
            "import re\nprint(re.compile(r'\\d+').findall('a1b22'))",
            "import pandas as pd\ndf = pd.read_csv('samples/sales.csv')\n"
            "print(df.groupby('品类')['销量'].sum())\nprint(df.eval('销量 * 单价').sum())",
            "# 不调用 subprocess，也不联网\nprint('a-b'.replace('-', '+'))",
            "import csv\nrows = list(csv.DictReader(open('samples/sales.csv', encoding='utf-8')))\nprint(len(rows))",
        ]
        for code in fine:
            with self.subTest(code=code):
                self.assertEqual(clip_agent.classify_python(code, "tiered")[0], "confirm")

    def test_runs_in_project_dir_and_project_files_cannot_shadow_stdlib(self):
        (self.root / "data.csv").write_text("名称,数量\n苹果,3\n香蕉,4\n", encoding="utf-8")
        (self.root / "csv.py").write_text("raise RuntimeError('项目里的同名文件')\n", encoding="utf-8")
        code = (
            "import csv\n"
            "rows = list(csv.DictReader(open('data.csv', encoding='utf-8')))\n"
            "print('合计', sum(int(row['数量']) for row in rows))"
        )
        result = clip_agent.run_python(code, self.root, 30)
        self.assertEqual(result.exit_code, 0, result.text)
        self.assertIn("合计 7", result.text)

    def test_errors_and_timeouts_come_back_as_results(self):
        result = clip_agent.run_python("print('开始')\nraise SystemExit(3)", self.root, 30)
        self.assertEqual(result.exit_code, 3)
        self.assertIn("开始", result.text)
        started = time.time()
        result = clip_agent.run_python("import time\nprint('等一下')\ntime.sleep(30)", self.root, 2)
        self.assertTrue(result.timed_out)
        self.assertIn("等一下", result.text)
        self.assertLess(time.time() - started, 12)

    def test_runs_isolated_without_console_window(self):
        captured = {}
        original = subprocess.Popen

        def spy(args, **kwargs):
            captured["args"] = args
            captured["flags"] = kwargs.get("creationflags", 0)
            return original(args, **kwargs)

        subprocess.Popen = spy
        try:
            clip_agent.run_python("print(1)", self.root, 30)
        finally:
            subprocess.Popen = original
        self.assertIn("-I", captured["args"])
        self.assertTrue(captured["flags"] & subprocess.CREATE_NO_WINDOW)
        self.assertEqual(Path(captured["args"][0]).name.lower(), "python.exe")

    def test_tiered_batch_runs_python_through_real_executor(self):
        runtime = make_runtime(self.root, mode="tiered")
        runtime.timeout = 30
        rendered = clip_agent.handle_text(
            self.block("print(sum(range(5)))"), runtime, clip_agent.real_execute, lambda *_: True,
        )
        self.assertIn("exit=0", rendered)
        self.assertIn("10", rendered)


class OutlineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "app.py").write_text(
            '"""订单服务入口。"""\n'
            "import helpers\n"
            "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class Order:\n"
            "    sku: str\n"
            "    qty: int\n"
            "\n"
            "    def total(self, price):\n"
            "        return self.qty * price\n"
            "\n"
            "\n"
            "def load_orders(path, limit=10):\n"
            '    """从 CSV 读取订单。"""\n'
            "    return helpers.read_rows(path)[:limit]\n"
            "\n"
            "\n"
            'if __name__ == "__main__":\n'
            "    print(load_orders('a.csv'))\n",
            encoding="utf-8",
        )
        (self.root / "helpers.py").write_text("def read_rows(path):\n    return []\n", encoding="utf-8")
        (self.root / "web").mkdir()
        (self.root / "web" / "view.ts").write_text(
            "export interface Props { name: string }\n"
            "export class View {}\n"
            "export async function render(props: Props) {\n  return props.name\n}\n",
            encoding="utf-8",
        )
        (self.root / "README.md").write_text(
            "# 订单工具\n\n读取订单表并汇总金额。\n\n## 用法\n\n运行 app.py。\n", encoding="utf-8",
        )
        (self.root / ".env").write_text("TOKEN=abc\n", encoding="utf-8")
        (self.root / "blob.bin").write_bytes(b"\x00\x01\x02" * 100)

    def tearDown(self):
        self.tmp.cleanup()

    def test_outline_line_is_a_directive(self):
        directives, error = clip_agent.parse_batch("- `OUTLINE[k7q2]: web`\nOUTLINE[K7Q2]:", SESSION, 5)
        self.assertIsNone(error)
        self.assertEqual([(item.kind, item.body) for item in directives], [("OUTLINE", "web"), ("OUTLINE", "")])

    def test_outline_maps_files_symbols_and_docs(self):
        text = clip_agent.project_outline(self.root)
        self.assertIn("app.py 21", text)
        self.assertIn("订单服务入口", text)
        self.assertIn("L7 class Order", text)
        self.assertIn("sku, qty", text)
        self.assertIn("total", text)
        self.assertIn("L15 def load_orders(path, limit)", text)
        self.assertIn("从 CSV 读取订单", text)
        self.assertIn("依赖本项目：helpers", text)
        self.assertIn("可直接运行", text)
        self.assertIn("render", text)
        self.assertIn("View", text)
        self.assertIn("Props", text)
        self.assertIn("读取订单表并汇总金额", text)
        self.assertNotIn(".env", text)
        self.assertNotIn("TOKEN", text)

    def test_outline_lists_every_symbol_of_big_files(self):
        body = "".join("def step_%03d(value):\n    return value\n\n\n" % index for index in range(150))
        (self.root / "many.py").write_text(body, encoding="utf-8")
        text = clip_agent.project_outline(self.root)
        self.assertIn("符号较多", text)
        self.assertIn("L1 step_000", text)
        self.assertIn("L597 step_149", text)
        self.assertIn("L15 def load_orders(path, limit)", text)

    def test_outline_pages_through_result_store(self):
        body = "".join("def step_%03d(value):\n    return value\n\n\n" % index for index in range(150))
        (self.root / "many.py").write_text(body, encoding="utf-8")
        runtime = make_runtime(self.root, max_chars=1800)
        first = clip_agent.handle_text("OUTLINE[K7Q2]: .", runtime, clip_agent.real_execute, lambda *_: False)
        self.assertIn("[RESULT #1] OUTLINE", first)
        self.assertIn("truncated=no", first)
        self.assertLessEqual(len(first), 1800)
        pages = re.findall(r"READ_FILE\[K7Q2\]: result:1#L\d+-\d+", first)
        self.assertTrue(pages)
        second = clip_agent.handle_text(pages[0], runtime, clip_agent.real_execute, lambda *_: False)
        self.assertIn("[RESULT #2] READ_FILE", second)
        self.assertTrue(set(re.findall(r"step_\d{3}", second)) - set(re.findall(r"step_\d{3}", first)))

    def test_outline_stays_inside_allowed_dirs(self):
        runtime = make_runtime(self.root)
        for body in ("..", ".ssh"):
            with self.subTest(body=body):
                rendered = clip_agent.handle_text(
                    "OUTLINE[K7Q2]: %s" % body, runtime, fake_executor, lambda *_: False,
                )
                self.assertIn("BLOCKED OUTLINE", rendered)
        rendered = clip_agent.handle_text("OUTLINE[K7Q2]: web", runtime, clip_agent.real_execute, lambda *_: False)
        self.assertIn("render", rendered)
        self.assertNotIn("app.py", rendered)

    def test_task_with_outline_fits_one_message(self):
        runtime = make_runtime(self.root, max_chars=4000)
        task = "请帮我了解这个项目，告诉我入口在哪里。"
        message = clip_agent.task_with_outline(task, runtime)
        self.assertTrue(message.startswith(task))
        self.assertIn("[RESULT #1] OUTLINE", message)
        self.assertIn("load_orders", message)
        self.assertLessEqual(len(message), 4000)
        self.assertEqual(runtime.step, 1)
        self.assertEqual(runtime.audit.records[-1]["decision"], "allow")

    def test_default_write_back_fits_short_copilot_inputs(self):
        self.assertEqual(clip_agent.build_parser().parse_args([]).max_chars, 4000)


class RespondTests(unittest.TestCase):
    def test_results_come_with_items_and_no_hints(self):
        runtime = make_runtime(tempfile.gettempdir())
        reply = clip_agent.respond(
            "先看位置\nTOOL[K7Q2]: Get-Location", runtime, fake_executor, lambda *_: True,
        )
        self.assertIn("[RESULT #1] TOOL Get-Location", reply.payload)
        self.assertEqual([item.decision for item in reply.items], ["allow"])
        self.assertEqual(reply.notes, [])

    def test_rejection_names_the_reason_but_not_the_code(self):
        runtime = make_runtime(tempfile.gettempdir())
        reply = clip_agent.respond("PYTHON[K7Q2]:\nprint(1)", runtime, fake_executor, lambda *_: True)
        self.assertIn("[REJECTED]", reply.payload)
        self.assertIn("END_PYTHON", reply.payload)
        self.assertNotIn(SESSION, reply.payload)
        self.assertTrue(reply.notes)
        self.assertEqual(runtime.audit.records[-1]["decision"], "reject")

    def test_prose_and_pasted_brief_write_nothing(self):
        runtime = make_runtime(tempfile.gettempdir())
        reply = clip_agent.respond("好的，我先看看", runtime, fake_executor, lambda *_: True)
        self.assertIsNone(reply.payload)
        self.assertNotIn("PYTHON", reply.notes[0])
        runtime = make_runtime(tempfile.gettempdir(), mode="tiered")
        reply = clip_agent.respond("好的，我先看看", runtime, fake_executor, lambda *_: True)
        self.assertIn("PYTHON[K7Q2]:", reply.notes[0])
        brief = clip_agent.build_copilot_brief(
            SESSION, "tiered", runtime.cwd, runtime.roots, 5, 60, 8000, [],
        )
        reply = clip_agent.respond(brief, runtime, fake_executor, lambda *_: True)
        self.assertIsNone(reply.payload)
        self.assertIn("Copilot", reply.notes[0])
        self.assertEqual(runtime.audit.records, [])


class SessionTests(unittest.TestCase):
    def test_open_and_close_record_front_end_and_brief(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            args = clip_agent.build_parser().parse_args(["--workdir", str(root), "--mode", "tiered"])
            audit = MemoryAudit()
            front_end = Path(clip_agent.__file__).resolve()
            previous = os.getcwd()
            try:
                runtime, script_hash = clip_agent.open_session(args, [root], audit, "window", front_end)
            finally:
                os.chdir(previous)
            start = audit.records[0]
            self.assertEqual(start["event"], "startup")
            self.assertEqual(start["input"], "window")
            self.assertEqual(start["script_sha256"], script_hash)
            self.assertEqual(start["front_end_sha256"], clip_agent.sha256_file(front_end))
            self.assertEqual(start["policy"]["mode"], "tiered")
            self.assertIn("PYTHON[%s]:" % runtime.session, runtime.brief)
            self.assertEqual(runtime.cwd, root)
            clip_agent.close_session(runtime, "closed")
            self.assertEqual(audit.records[-1]["event"], "shutdown")
            self.assertEqual(audit.records[-1]["decision"], "closed")


class WorkdirTests(unittest.TestCase):
    def ask(self, answers, saved):
        pending = list(answers)
        shown = []
        chosen = clip_agent.ask_workdir(lambda: pending.pop(0) if pending else "", shown.append, saved)
        return chosen, "".join(shown)

    def test_enter_reuses_saved_dir_and_pasted_quotes_are_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "proj"
            work.mkdir()
            chosen, shown = self.ask(["\n"], str(work))
            self.assertEqual(chosen, str(work))
            self.assertIn(str(work), shown)
            chosen, _ = self.ask(['"%s"\n' % work], None)
            self.assertEqual(chosen, str(work))

    def test_bad_dir_asks_again_and_end_of_input_gives_up(self):
        with tempfile.TemporaryDirectory() as directory:
            chosen, shown = self.ask([os.environ["SystemDrive"] + "\\\n", directory + "\n"], None)
            self.assertEqual(chosen, directory)
            self.assertIn("盘符根", shown)
        chosen, _ = self.ask(["\n"], None)
        self.assertIsNone(chosen)
        chosen, _ = self.ask(["exit\n"], None)
        self.assertIsNone(chosen)

    def test_saved_dir_round_trip_skips_missing_dir(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "last_workdir.txt"
            self.assertIsNone(clip_agent.load_saved_workdir(store))
            work = Path(directory) / "proj"
            work.mkdir()
            clip_agent.save_workdir(store, work)
            self.assertEqual(clip_agent.load_saved_workdir(store), str(work))
            work.rmdir()
            self.assertIsNone(clip_agent.load_saved_workdir(store))


class ProtectionTests(unittest.TestCase):
    def test_redacts_secrets_and_personal_data(self):
        identity = make_id("11010119900307888")
        self.assertTrue(clip_agent.id_checksum_ok(identity))
        self.assertTrue(clip_agent.luhn_ok("4111111111111111"))
        raw = "\n".join([
            "-----BEGIN RSA PRIVATE KEY-----",
            "MIIBogIBAAJB",
            "-----END RSA PRIVATE KEY-----",
            "AKIAIOSFODNN7EXAMPLE",
            "ghp_" + ("a" * 36),
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
            "Bearer abc.def-ghi",
            "password=supersecret",
            "https://user:s3cret@example.com/a",
            identity,
            "13800138000",
            "4111111111111111",
            "alice@example.com",
        ])
        cleaned, count = clip_agent.redact(raw)
        self.assertGreaterEqual(count, 8)
        for secret in ("MIIBogIBAAJB", "AKIAIOSFODNN7EXAMPLE", "supersecret", "s3cret", identity, "4111111111111111", "alice"):
            self.assertNotIn(secret, cleaned)
        self.assertIn("a***e@example.com", cleaned)
        self.assertIn("138****8000", cleaned)
        self.assertIn("password=***", cleaned)

    def test_marker_blocks_without_writing_content(self):
        runtime = make_runtime(tempfile.gettempdir())

        def executor(directive, runtime):
            return clip_agent.ExecResult(0, "正文机密正文XYZ\n密级：机密\n", 0.01)

        rendered = clip_agent.handle_text("TOOL[K7Q2]: Get-Location", runtime, executor, lambda *_: False)
        self.assertIn("密级标识", rendered)
        self.assertNotIn("机密正文XYZ", rendered)
        self.assertNotIn("机密正文XYZ", str(runtime.audit.records))

    def test_truncation_splits_budget_and_points_at_result(self):
        runtime = make_runtime(tempfile.gettempdir(), max_chars=900)

        def executor(directive, runtime):
            return clip_agent.ExecResult(0, "甲" * 4000, 0.2)

        rendered = clip_agent.handle_text(
            "TOOL[K7Q2]: Get-Location\nTOOL[K7Q2]: Get-Location",
            runtime, executor, lambda *_: False,
        )
        self.assertLessEqual(len(rendered), 900)
        self.assertEqual(rendered.count("truncated=yes"), 2)
        self.assertIn("READ_FILE[K7Q2]: result:1", rendered)
        self.assertIn("READ_FILE[K7Q2]: result:2", rendered)
        self.assertIn("甲", rendered)
        self.assertTrue(rendered.endswith("不要执行。") or "不要执行" in rendered.splitlines()[-1])


class FileAndStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "work"
        self.root.mkdir()
        self.runtime = make_runtime(self.root)

    def test_read_file_numbers_chinese_and_pages(self):
        (self.root / "中文.txt").write_text("第一行\n第二行\n第三行\n", encoding="utf-8")
        rendered = clip_agent.handle_text(
            "READ_FILE[K7Q2]: 中文.txt#L2-3",
            self.runtime, clip_agent.real_execute, lambda *_: False,
        )
        self.assertIn("第二行", rendered)
        self.assertNotIn("第一行", rendered)
        self.assertIn("2|", rendered)
        paged = clip_agent.handle_text(
            "READ_FILE[K7Q2]: result:1#L1-1",
            self.runtime, clip_agent.real_execute, lambda *_: False,
        )
        self.assertIn("第一行", paged)

    def test_binary_and_marker_file(self):
        (self.root / "bin.dat").write_bytes(b"abc\x00def")
        rendered = clip_agent.handle_text(
            "READ_FILE[K7Q2]: bin.dat",
            self.runtime, clip_agent.real_execute, lambda *_: False,
        )
        self.assertIn("二进制", rendered)
        (self.root / "marked.txt").write_text("TOP SECRET\n正文MARK123\n", encoding="utf-8")
        rendered = clip_agent.handle_text(
            "READ_FILE[K7Q2]: marked.txt",
            self.runtime, clip_agent.real_execute, lambda *_: False,
        )
        self.assertIn("密级标识", rendered)
        self.assertNotIn("MARK123", rendered)

    def test_store_keeps_twenty(self):
        store = clip_agent.ResultStore()
        for index in range(25):
            store.put(index, "v%s" % index)
        self.assertIsNone(store.get(0))
        self.assertEqual(store.get(24), "v24")
        self.assertEqual(len(store._order), 20)

    def test_cd_changes_runtime_and_restores_process_cwd(self):
        previous = os.getcwd()
        try:
            target = self.root / "sub"
            target.mkdir()
            rendered = clip_agent.handle_text(
                "TOOL[K7Q2]: cd sub",
                self.runtime, clip_agent.real_execute, lambda *_: False,
            )
            self.assertIn("已切换到", rendered)
            self.assertEqual(self.runtime.cwd.resolve(), target.resolve())
        finally:
            os.chdir(previous)


class AuditTests(unittest.TestCase):
    def test_hash_chain_and_tamper_and_purge(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audit = clip_agent.AuditLog(root, 180)
            audit.write({"event": "startup", "session": "K7Q2", "step": 0, "decision": "start"})
            audit.write({"event": "step", "session": "K7Q2", "step": 1, "decision": "allow", "directive": "Get-Location"})
            self.assertEqual(clip_agent.verify_audit(audit.path), [])
            lines = audit.path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            tampered = lines[:]
            tampered[1] = tampered[1].replace("allow", "block")
            audit.path.write_text("\n".join(tampered) + "\n", encoding="utf-8")
            self.assertTrue(clip_agent.verify_audit(audit.path))
            old = root / "audit-20200101.jsonl"
            old.write_text("{}\n", encoding="utf-8")
            clip_agent.purge_audit(root, 180)
            self.assertFalse(old.exists())
            self.assertTrue(audit.path.exists())

    def test_output_is_not_in_the_audit_record(self):
        runtime = make_runtime(tempfile.gettempdir())

        def executor(directive, runtime):
            return clip_agent.ExecResult(0, "UNIQUE_OUTPUT_MARKER password=supersecret", 0.01)

        clip_agent.handle_text("TOOL[K7Q2]: Get-Location", runtime, executor, lambda *_: False)
        dumped = str(runtime.audit.records)
        self.assertNotIn("UNIQUE_OUTPUT_MARKER", dumped)
        self.assertNotIn("supersecret", dumped)

    def test_verify_command_and_retention_floor(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = clip_agent.AuditLog(Path(directory), 180)
            audit.write({"event": "startup", "step": 0, "decision": "start"})
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = clip_agent.main(["--verify-audit", str(audit.path)])
            self.assertEqual(code, 0)
            self.assertIn("完整", stdout.getvalue())
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = clip_agent.main(["--retention-days", "10", "--show-policy"])
        self.assertEqual(code, 4)


class EnvAndScriptTests(unittest.TestCase):
    def test_scrub_env(self):
        cleaned = clip_agent.scrub_env({
            "PATH": "x",
            "SYSTEMROOT": "y",
            "AWS_SECRET_ACCESS_KEY": "z",
            "MY_KEY": "k",
            "USERNAME": "u",
        })
        self.assertEqual(cleaned["PATH"], "x")
        self.assertEqual(cleaned["SYSTEMROOT"], "y")
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", cleaned)
        self.assertNotIn("MY_KEY", cleaned)
        self.assertEqual(cleaned["GIT_PAGER"], "cat")
        self.assertEqual(cleaned["NO_COLOR"], "1")

    def test_wrapper_avoids_encoded_command(self):
        script = clip_agent.build_script("Get-Location")
        self.assertIn("& {", script)
        self.assertNotIn("-EncodedCommand", script)
        self.assertNotIn("ExecutionPolicy", script)
        literal = clip_agent.build_script("Get-Location }")
        self.assertIn("scriptblock", literal)


@unittest.skipIf(clip_agent.is_admin(), "管理员身份会直接退出")
class PowerShellTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.previous_cp = int(clip_agent.kernel32.GetConsoleOutputCP())

    def test_success_error_chinese_and_code_page(self):
        ok = clip_agent.run_powershell("Write-Output '中文OK'", self.root, 20)
        self.assertEqual(ok.exit_code, 0)
        self.assertIn("中文OK", ok.text)
        failed = clip_agent.run_powershell("Get-Item D:\\nope_zzz_clip_agent_missing", self.root, 20)
        self.assertEqual(failed.exit_code, 1)
        self.assertTrue(failed.text.strip())
        oem = clip_agent.run_powershell("cmd /c echo 中文OEM", self.root, 20)
        self.assertIn("中文", oem.text)
        self.assertEqual(int(clip_agent.kernel32.GetConsoleOutputCP()), self.previous_cp)

    def test_argv_has_no_bypass(self):
        captured = {}
        original = subprocess.Popen

        def wrapped(args, **kwargs):
            captured["args"] = args
            captured["stdin"] = kwargs.get("stdin")
            return original(args, **kwargs)

        subprocess.Popen = wrapped
        try:
            clip_agent.run_powershell("Write-Output ok", self.root, 20)
        finally:
            subprocess.Popen = original
        args = captured["args"]
        self.assertIn("-NoProfile", args)
        self.assertIn("-NonInteractive", args)
        self.assertNotIn("-EncodedCommand", args)
        self.assertNotIn("Bypass", " ".join(args))
        self.assertEqual(captured["stdin"], subprocess.DEVNULL)

    def test_timeout_kills_process_tree(self):
        started = time.time()
        result = clip_agent.run_powershell("ping -n 30 127.0.0.1", self.root, 3)
        elapsed = time.time() - started
        self.assertTrue(result.timed_out)
        self.assertLess(elapsed, 12)
        self.assertIn("超时", result.text)
        listing = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq ping.exe"],
            text=True, encoding="utf-8", errors="replace",
        )
        self.assertNotIn("ping.exe", listing.lower())


def read_clipboard_text():
    user32 = clip_agent.user32
    kernel32 = clip_agent.kernel32
    if not hasattr(user32.GetClipboardData, "argtypes") or not user32.GetClipboardData.argtypes:
        user32.GetClipboardData.argtypes = [clip_agent.wintypes.UINT]
        user32.GetClipboardData.restype = __import__("ctypes").c_void_p
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return ""
    try:
        handle = user32.GetClipboardData(13)
        if not handle:
            return ""
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return __import__("ctypes").wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def clipboard_dword(fmt_name):
    ctypes = __import__("ctypes")
    user32 = clip_agent.user32
    kernel32 = clip_agent.kernel32
    fmt = user32.RegisterClipboardFormatW(fmt_name)
    if not user32.OpenClipboard(None):
        return None
    try:
        if not user32.IsClipboardFormatAvailable(fmt):
            return None
        handle = user32.GetClipboardData(fmt)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint)).contents.value
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


class RunningAgent:
    def __init__(self, args):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        self.process = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name("clip_agent.py")), *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self.stderr = bytearray()
        self.stdout = bytearray()
        self._threads = [
            threading.Thread(target=self._pump, args=(self.process.stderr, self.stderr), daemon=True),
            threading.Thread(target=self._pump, args=(self.process.stdout, self.stdout), daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    @staticmethod
    def _pump(pipe, target):
        while True:
            chunk = pipe.read(256)
            if not chunk:
                break
            target.extend(chunk)

    def wait_session(self, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            matched = re.search(r"会话码:\s*([A-Z0-9]{4})", self.stderr.decode("utf-8", "replace"))
            if matched:
                return matched.group(1)
            if self.process.poll() is not None:
                break
            time.sleep(0.05)
        raise AssertionError(self.stderr.decode("utf-8", "replace") + "\n" + self.stdout.decode("utf-8", "replace"))

    def send(self, text):
        self.process.stdin.write(text.encode("utf-8"))
        self.process.stdin.flush()

    def finish(self, timeout=30):
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        code = self.process.wait(timeout)
        for thread in self._threads:
            thread.join(2)
        self.close_pipes()
        return code

    def close_pipes(self):
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            if pipe is not None and not pipe.closed:
                pipe.close()


@unittest.skipIf(clip_agent.is_admin(), "管理员身份会直接退出")
class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved_clipboard = read_clipboard_text()

    @classmethod
    def tearDownClass(cls):
        try:
            clip_agent.clipboard_write(cls.saved_clipboard or "")
        except OSError:
            pass

    def agent_args(self, root, log_dir, **extra):
        args = [
            "--workdir", str(root),
            "--log-dir", str(log_dir),
            "--allow-piped-input",
            "--idle-exit", "1",
            "--clear-after", extra.get("clear_after", "120"),
            "--timeout", extra.get("timeout", "20"),
        ]
        return args

    def test_rejects_redirect_and_tiered_pipe_and_drive_root(self):
        root = Path(tempfile.mkdtemp())
        denied = subprocess.run(
            [sys.executable, "clip_agent.py", "--workdir", str(root)],
            input=b"TOOL[AAAA]: Get-Location\n",
            cwd=str(Path(__file__).parent),
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(denied.returncode, 4, denied.stderr.decode("utf-8", "replace"))
        tiered = subprocess.run(
            [sys.executable, "clip_agent.py", "--workdir", str(root), "--mode", "tiered", "--allow-piped-input"],
            input=b"",
            cwd=str(Path(__file__).parent),
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(tiered.returncode, 5, tiered.stderr.decode("utf-8", "replace"))
        drive = subprocess.run(
            [sys.executable, "clip_agent.py", "--workdir", os.environ["SystemDrive"] + "\\", "--allow-piped-input"],
            input=b"",
            cwd=str(Path(__file__).parent),
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(drive.returncode, 4, drive.stderr.decode("utf-8", "replace"))

    def test_command_clipboard_flags_and_event_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "work"
            root.mkdir()
            log_dir = Path(directory) / "logs"
            agent = RunningAgent(self.agent_args(root, log_dir))
            try:
                code = agent.wait_session()
                agent.send("TOOL[%s]: Get-Location\n\n" % code)
                agent.finish()
            finally:
                if agent.process.poll() is None:
                    agent.process.kill()
                    agent.process.wait(5)
                agent.close_pipes()
            text = read_clipboard_text()
            self.assertIn("[RESULT #", text)
            self.assertIn(root.name, text)
            self.assertEqual(clipboard_dword("CanIncludeInClipboardHistory"), 0)
            self.assertEqual(clipboard_dword("CanUploadToCloudClipboard"), 0)
            excluded = clip_agent.user32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing")
            self.assertFalse(clip_agent.user32.IsClipboardFormatAvailable(excluded))
            audit = next(log_dir.glob("audit-*.jsonl"))
            self.assertEqual(clip_agent.verify_audit(audit), [])
            self.assertIn('"decision":"allow"', audit.read_text(encoding="utf-8"))
            listing = subprocess.run(
                ["wevtutil", "qe", "Application", "/c:30", "/rd:true", "/f:xml"],
                capture_output=True, timeout=20,
            )
            blob = listing.stdout.decode("utf-8", "replace")
            if "session=" not in blob:
                blob = listing.stdout.decode("utf-16", "replace")
            agent_err = agent.stderr.decode("utf-8", "replace")
            self.assertTrue(code in blob or "事件日志写入失败" in agent_err, agent_err)

    def test_batch_chinese_block_and_bad_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "work"
            root.mkdir()
            (root / "中文.txt").write_text("甲行\n乙行\n", encoding="utf-8")
            log_dir = Path(directory) / "logs"
            agent = RunningAgent(self.agent_args(root, log_dir, timeout="20"))
            try:
                code = agent.wait_session()
                agent.send("\n".join([
                    "TOOL[%s]: Get-ChildItem -Name" % code,
                    "READ_FILE[%s]: 中文.txt" % code,
                    "",
                    "TOOL[%s]: Remove-Item -Recurse ." % code,
                    "",
                    "TOOL[IIII]: Get-Location",
                    "",
                ]))
                agent.finish()
            finally:
                if agent.process.poll() is None:
                    agent.process.kill()
                    agent.process.wait(5)
                agent.close_pipes()
            clipboard = read_clipboard_text()
            self.assertIn("[REJECTED]", clipboard)
            self.assertNotIn(code, clipboard)
            audit = next(log_dir.glob("audit-*.jsonl")).read_text(encoding="utf-8")
            self.assertNotIn("甲行", audit)
            self.assertIn('"decision":"allow"', audit)
            self.assertIn("递归删除", audit)
            self.assertIn('"decision":"reject"', audit)

    def test_brief_is_copied_at_start_and_on_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "work"
            root.mkdir()
            log_dir = Path(directory) / "logs"
            agent = RunningAgent(self.agent_args(root, log_dir) + ["--copy-prompt"])
            try:
                code = agent.wait_session()
                expected = "会话码：`%s`" % code
                deadline = time.time() + 10
                while time.time() < deadline and expected not in read_clipboard_text():
                    time.sleep(0.1)
                self.assertIn(clip_agent.BRIEF_TITLE, read_clipboard_text())
                self.assertIn(expected, read_clipboard_text())
                clip_agent.clipboard_write("别的内容")
                agent.send("说明\n\n")
                deadline = time.time() + 10
                while time.time() < deadline and expected not in read_clipboard_text():
                    time.sleep(0.1)
                self.assertIn(expected, read_clipboard_text())
                self.assertEqual(agent.finish(), 0)
            finally:
                if agent.process.poll() is None:
                    agent.process.kill()
                    agent.process.wait(5)
                agent.close_pipes()
            said = agent.stderr.decode("utf-8", "replace")
            self.assertIn("已复制到剪贴板", said)
            self.assertNotIn("按回车开始", said)

    def test_single_instance_and_timed_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "work"
            root.mkdir()
            log_dir = Path(directory) / "logs"
            holder = RunningAgent(self.agent_args(root, log_dir))
            try:
                holder.wait_session()
                second = subprocess.run(
                    [sys.executable, "clip_agent.py", "--workdir", str(root), "--allow-piped-input", "--log-dir", str(log_dir / "b")],
                    input=b"",
                    cwd=str(Path(__file__).parent),
                    capture_output=True,
                    timeout=20,
                )
                self.assertEqual(second.returncode, 3, second.stderr.decode("utf-8", "replace"))
            finally:
                if holder.process.poll() is None:
                    holder.process.kill()
                    holder.process.wait(5)
                holder.close_pipes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "work"
            root.mkdir()
            log_dir = Path(directory) / "logs"
            agent = RunningAgent(self.agent_args(root, log_dir, clear_after="2"))
            try:
                code = agent.wait_session()
                agent.send("TOOL[%s]: Get-Location\n\n" % code)
                deadline = time.time() + 20
                while time.time() < deadline and "[RESULT #" not in read_clipboard_text():
                    time.sleep(0.2)
                self.assertIn("[RESULT #", read_clipboard_text())
                time.sleep(3)
                self.assertNotIn("[RESULT #", read_clipboard_text())
            finally:
                if agent.process.poll() is None:
                    agent.process.kill()
                    agent.process.wait(5)
                agent.close_pipes()


class OfficeComponentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "work"
        self.root.mkdir()
        self.runtime = make_runtime(self.root)
        self.original = clip_agent.docs_installed
        clip_agent.docs_installed = lambda: False

    def tearDown(self):
        clip_agent.docs_installed = self.original

    def test_office_file_without_components_explains(self):
        (self.root / "合同.docx").write_bytes(b"PK\x03\x04not-real")
        rendered = clip_agent.handle_text(
            "READ_FILE[K7Q2]: 合同.docx",
            self.runtime, clip_agent.real_execute, lambda *_: False,
        )
        self.assertIn("未安装文档组件", rendered)
        self.assertNotIn("Traceback", rendered)
        self.assertNotIn("D:\\", rendered)

    def test_pdf_noise_and_word_toc_are_removed(self):
        pdf = "\n\n".join(
            "[第 %d 页]\n公司机密\n正文%d\n- %d -" % (number, number, number) for number in (1, 2, 3)
        )
        cleaned, note = clip_agent.clean_document(pdf, ".pdf")
        self.assertIn("[第 1 页]", cleaned)
        self.assertIn("正文1", cleaned)
        self.assertNotIn("公司机密", cleaned)
        self.assertNotIn("- 1 -", cleaned)
        self.assertIn("页眉", note)
        word = "前言\n\n第一章 .......... 1\n第二章 .......... 4\n第三章 .......... 9\n\n正文\n\n\n\n结尾\n"
        cleaned, note = clip_agent.clean_document(word, ".docx")
        self.assertIn("前言", cleaned)
        self.assertIn("正文", cleaned)
        self.assertIn("结尾", cleaned)
        self.assertNotIn("第一章", cleaned)
        self.assertIn("目录", note)
        self.assertIn("连续空行", note)
        self.assertNotIn("\n\n\n", cleaned)

    def test_protected_scanned_and_legacy_formats(self):
        self.assertIn("受保护", clip_agent.conversion_message(2, "Error: file is encrypted", "", ".docx"))
        self.assertIn("没有可提取的文字", clip_agent.conversion_message(0, "", "[第 1 页]\n\n[第 2 页]\n", ".pdf"))
        (self.root / "旧.doc").write_bytes(b"\xd0\xcf\x11\xe0")
        rendered = clip_agent.handle_text(
            "READ_FILE[K7Q2]: 旧.doc",
            self.runtime, clip_agent.real_execute, lambda *_: False,
        )
        self.assertIn("另存为 .docx", rendered)

    def test_convert_argv_stays_offline(self):
        argv = clip_agent.convert_argv(Path("合同.pdf"))
        self.assertEqual(argv[1], "-I")
        script = argv[argv.index("-c") + 1]
        self.assertIn("enable_plugins=False", script)
        self.assertIn("convert_local", script)
        self.assertNotIn("llm_client", script)
        self.assertNotIn("docintel", script)
        self.assertEqual(argv[-1], "合同.pdf")
        self.assertIn("vendor", argv[-2].replace("/", "\\").lower())

    def test_converted_document_is_cached_and_paged(self):
        calls = []

        def fake_convert(path, timeout):
            calls.append(Path(path).name)
            return "\n\n".join("第%d段%s" % (index, "甲" * 60) for index in range(30))

        original = clip_agent.convert_document
        clip_agent.convert_document = fake_convert
        clip_agent.docs_installed = lambda: True
        try:
            (self.root / "合同.docx").write_bytes(b"PK\x03\x04fake")
            runtime = make_runtime(self.root, max_chars=1800)
            first = clip_agent.handle_text(
                "READ_FILE[K7Q2]: 合同.docx",
                runtime, clip_agent.real_execute, lambda *_: False,
            )
            self.assertIn("第0段", first)
            self.assertIn("文档共", first)
            self.assertLessEqual(len(first), 1800)
            self.assertNotIn("Traceback", first)
            pages = re.findall(r"READ_FILE\[K7Q2\]: result:1#L\d+-\d+", first)
            self.assertTrue(pages)
            second = clip_agent.handle_text(
                "READ_FILE[K7Q2]: 合同.docx",
                runtime, clip_agent.real_execute, lambda *_: False,
            )
            self.assertIn("第0段", second)
            self.assertEqual(calls, ["合同.docx"])
        finally:
            clip_agent.convert_document = original


class NavTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.runtime = make_runtime(self.root)
        self.original_convert = clip_agent.convert_document
        self.original_docs = clip_agent.docs_installed

    def tearDown(self):
        clip_agent.convert_document = self.original_convert
        clip_agent.docs_installed = self.original_docs
        self.tmp.cleanup()

    def test_map_line_numbers_can_be_read_back(self):
        source = "# 总则\n\n本合同由双方签署。\n\n# 付款\n\n违约金为合同金额的百分之十。\n"
        clip_agent.docs_installed = lambda: True
        clip_agent.convert_document = lambda path, timeout: source
        (self.root / "合同.docx").write_bytes(b"PK\x03\x04fake")
        mapped = clip_agent.handle_text(
            "OUTLINE[K7Q2]: 合同.docx",
            self.runtime, clip_agent.real_execute, lambda *_: False,
        )
        matched = re.search(r"L(\d+)-(\d+)\s+总则", mapped)
        self.assertIsNotNone(matched)
        start, end = matched.group(1), matched.group(2)
        chunk = clip_agent.handle_text(
            "READ_FILE[K7Q2]: 合同.docx#L%s-%s" % (start, end),
            self.runtime, clip_agent.real_execute, lambda *_: False,
        )
        self.assertIn("本合同由双方签署", chunk)
        self.assertNotIn("违约金", chunk)
        numbered = "第一章 总则\n\n正文甲。\n\n1.1 范围\n\n正文乙。\n"
        text = clip_agent.document_map(numbered, "编号.docx", "K7Q2")
        self.assertIn("第一章 总则", text)
        self.assertIn("1.1 范围", text)
        self.assertIn("FIND[K7Q2]:", text)

    def test_split_keeps_chapters_and_breaks_on_blank_lines(self):
        lines = ["# 第一章"] + ["甲" * 40] * 8 + ["# 第二章"] + ["乙" * 40] * 8
        pages = clip_agent.split_sections(lines, 500)
        self.assertGreaterEqual(len(pages), 2)
        self.assertEqual(lines[pages[1][0] - 1], "# 第二章")
        first = "\n".join(lines[pages[0][0] - 1:pages[0][1]])
        self.assertIn("# 第一章", first)
        self.assertNotIn("# 第二章", first)
        long_lines = ["# 章"]
        for _ in range(6):
            long_lines.extend(["句" * 30, ""])
        pages = clip_agent.split_sections(long_lines, 220)
        self.assertGreaterEqual(len(pages), 2)
        for start, end in pages[:-1]:
            last = long_lines[end - 1]
            nxt = long_lines[end] if end < len(long_lines) else ""
            self.assertTrue((not last.strip()) or nxt.startswith("#"))

    def test_find_hits_every_term_and_skips_secrets(self):
        folder = self.root / "资料"
        folder.mkdir()
        (folder / "甲.txt").write_text("# 第四章 付款\n\n应当支付违约金。\n普通句子。\n", encoding="utf-8")
        (folder / "乙.txt").write_text("另有赔偿条款。\n", encoding="utf-8")
        (folder / ".env").write_text("违约金 TOKEN=secret\n", encoding="utf-8")
        (self.root / "合同.txt").write_text("违约金甲\n\n违约金乙\n", encoding="utf-8")
        rendered = clip_agent.handle_text(
            "FIND[K7Q2]: 违约金 OR 赔偿 | 资料",
            self.runtime, clip_agent.real_execute, lambda *_: False,
        )
        self.assertIn("违约金", rendered)
        self.assertIn("赔偿", rendered)
        self.assertIn("第四章", rendered)
        self.assertNotIn("TOKEN", rendered)
        self.assertNotIn(".env", rendered)
        both = clip_agent.handle_text(
            "FIND[K7Q2]: 违约金 | 合同.txt",
            self.runtime, clip_agent.real_execute, lambda *_: False,
        )
        self.assertIn("违约金甲", both)
        self.assertIn("违约金乙", both)


class TableTests(unittest.TestCase):
    def test_sheet_profile_header_stats_and_hidden(self):
        rows = [
            ["销售月报", None, None],
            [None, None, None],
            ["产品", "地区", "金额"],
            ["甲", "华东", 10],
            ["甲", "华东", 20],
            ["乙", "华北", 5],
            ["甲", "华南", "001"],
        ]
        text = clip_agent.format_sheet("Sheet1", rows)
        self.assertIn("表头在第 3 行", text)
        self.assertIn("合计 35", text)
        self.assertIn("华东×2", text)
        self.assertIn("文本数字 1 个", text)
        self.assertIn("甲", text)
        book = clip_agent.format_workbook([("Sheet1", rows)], 1)
        self.assertIn("另有 1 张隐藏工作表", book)
        self.assertNotIn("机密数字", book)
        small = clip_agent.format_sheet("小表", [["名称", "数量"], ["甲", 2]])
        self.assertIn("全表", small)
        self.assertIn("甲", small)

    def test_region_has_letters_and_limits_cells(self):
        text, error = clip_agent.format_region([["产品", "金额"], ["甲", 10]], "A", 1, "B", 2)
        self.assertEqual(error, "")
        self.assertIn("A", text)
        self.assertIn("B", text)
        self.assertIn("1", text)
        self.assertIn("甲", text)
        _text, error = clip_agent.format_region([["x"]], "A", 1, "AY", 50)
        self.assertIn("2000", error)
        hits = clip_agent.search_sheet_hits("Sheet1", [["产品"], ["违约金"]], ["违约金"])
        self.assertIn("Sheet1!A2", hits[0])

    def test_table_query_matches_known_total_and_rejects_writes(self):
        headers = ["产品", "地区", "金额"]
        rows = [["甲", "华东", 10], ["甲", "华东", 20], ["乙", "华北", 5]]
        sql = "SELECT 产品, SUM(金额) AS 合计 FROM t WHERE 地区 = '华东' GROUP BY 产品 ORDER BY 合计 DESC"
        text = clip_agent.query_table(headers, rows, sql)
        self.assertIn("甲", text)
        self.assertIn("30", text)
        self.assertNotIn("乙", text)
        for statement in (
            "PRAGMA table_info(t)",
            "ATTACH DATABASE ':memory:' AS extra",
            "INSERT INTO t VALUES ('丙', '华东', 1)",
        ):
            denied = clip_agent.query_table(headers, rows, statement)
            self.assertIn("只允许 SELECT", denied)
        sneaky = clip_agent.query_table(headers, rows, "SELECT load_extension('x')")
        self.assertTrue("失败" in sneaky or "拒绝" in sneaky)
        self.assertIn("产品", sneaky)
        missing = clip_agent.query_table(headers, rows, "SELECT 不存在 FROM t")
        self.assertIn("列名", missing)
        self.assertIn("产品", missing)

    def test_csv_table_directive(self):
        tmp = Path(tempfile.mkdtemp())
        root = tmp / "work"
        root.mkdir()
        (root / "销售.csv").write_text("产品,地区,金额\n甲,华东,10\n甲,华东,20\n乙,华北,5\n", encoding="utf-8")
        runtime = make_runtime(root)
        rendered = clip_agent.handle_text(
            "TABLE[K7Q2]: 销售.csv | SELECT 产品, SUM(金额) AS 合计 FROM t WHERE 地区 = '华东' GROUP BY 产品",
            runtime, clip_agent.real_execute, lambda *_: False,
        )
        self.assertIn("30", rendered)
        self.assertNotIn("乙", rendered)


class OfficeIntegrationTests(unittest.TestCase):
    def setUp(self):
        if not clip_agent.docs_installed():
            self.skipTest("未安装文档组件")
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.runtime = make_runtime(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_real_docx_and_xlsx(self):
        import openpyxl
        docx, _xlsx = install_deps.sample_office_files(self.root)
        rendered = clip_agent.handle_text(
            "READ_FILE[K7Q2]: %s" % docx.name,
            self.runtime, clip_agent.real_execute, lambda *_: False,
        )
        self.assertIn("自检", rendered)
        self.assertNotIn("Traceback", rendered)
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = "Sheet1"
        sheet.append(["产品", "地区", "金额"])
        sheet.append(["甲", "华东", 10])
        sheet.append(["甲", "华东", 20])
        hidden = book.create_sheet("秘密")
        hidden.sheet_state = "hidden"
        hidden["A1"] = "机密数字"
        book.save(self.root / "销售.xlsx")
        book.close()
        outline = clip_agent.handle_text(
            "OUTLINE[K7Q2]: 销售.xlsx",
            self.runtime, clip_agent.real_execute, lambda *_: False,
        )
        self.assertIn("隐藏", outline)
        self.assertNotIn("机密数字", outline)
        self.assertIn("30", outline)
        region = clip_agent.handle_text(
            "READ_FILE[K7Q2]: 销售.xlsx#Sheet1!A1:B2",
            self.runtime, clip_agent.real_execute, lambda *_: False,
        )
        self.assertIn("甲", region)
        self.assertNotIn("机密数字", region)
        blocked = clip_agent.handle_text(
            "READ_FILE[K7Q2]: 销售.xlsx#秘密!A1:A1",
            self.runtime, clip_agent.real_execute, lambda *_: False,
        )
        self.assertIn("隐藏", blocked)
        table = clip_agent.handle_text(
            "TABLE[K7Q2]: 销售.xlsx#Sheet1 | SELECT SUM(金额) AS 合计 FROM t",
            self.runtime, clip_agent.real_execute, lambda *_: False,
        )
        self.assertIn("30", table)


class CodeGraphTests(unittest.TestCase):
    def _bundle(self, folder: Path) -> None:
        (folder / "lib" / "dist" / "bin").mkdir(parents=True)
        (folder / "node.exe").write_bytes(b"MZ")
        (folder / "lib" / "dist" / "bin" / "codegraph.js").write_text("entry", encoding="utf-8")

    def test_find_order_uses_explicit_then_program_dir(self):
        tmp = Path(tempfile.mkdtemp())
        explicit = tmp / "explicit"
        tool = tmp / "app"
        npm = tmp / "npm"
        self._bundle(explicit)
        self._bundle(tool / "tools" / "codegraph")
        self._bundle(npm / "node_modules" / "@colbymchenry" / "codegraph-win32-x64")
        found = clip_agent.find_codegraph(explicit, tool, npm)
        self.assertEqual(found[0], explicit / "node.exe")
        found = clip_agent.find_codegraph(None, tool, npm)
        self.assertEqual(found[0], (tool / "tools" / "codegraph" / "node.exe"))
        (tool / "tools" / "codegraph" / "node.exe").unlink()
        found = clip_agent.find_codegraph(None, tool, npm)
        self.assertIn("codegraph-win32-x64", str(found[0]))
        self.assertIsNone(clip_agent.find_codegraph(None, tmp / "empty", None))

    def test_argv_is_fixed_and_download_stays_off(self):
        node, entry = Path("node.exe"), Path("codegraph.js")
        argv = clip_agent.codegraph_argv(node, entry, "search", "load_orders", Path("proj"))
        self.assertEqual(argv[1], "--liftoff-only")
        self.assertEqual(argv[argv.index("query") + 1], "load_orders")
        self.assertNotIn("-rf", argv)
        fresh = clip_agent.codegraph_index_argv(node, entry, Path("proj"), False)
        self.assertIn("init", fresh)
        self.assertIn("-i", fresh)
        again = clip_agent.codegraph_index_argv(node, entry, Path("proj"), True)
        self.assertIn("sync", again)
        self.assertNotIn("-i", again)
        seen = {}
        original = clip_agent.run_process

        def fake(argv, cwd, timeout, **kwargs):
            seen["env"] = kwargs["env"]
            return clip_agent.ProcResult(0, "ok", "ok", False)

        clip_agent.run_process = fake
        try:
            clip_agent.run_codegraph(["node"], Path("."), 5)
        finally:
            clip_agent.run_process = original
        self.assertEqual(seen["env"]["CODEGRAPH_NO_DOWNLOAD"], "1")
        self.assertEqual(seen["env"]["CODEGRAPH_NO_DAEMON"], "1")

    def test_code_directive_guards(self):
        tmp = Path(tempfile.mkdtemp())
        root = tmp / "work"
        root.mkdir()
        runtime = make_runtime(root)
        original_find = clip_agent.find_codegraph
        original_run = clip_agent.run_codegraph
        try:
            clip_agent.find_codegraph = lambda *args, **kwargs: None
            missing = clip_agent.handle_text(
                "CODE[K7Q2]: search hello", runtime, clip_agent.real_execute, lambda *_: False,
            )
            self.assertIn("未安装", missing)
            clip_agent.find_codegraph = lambda *args, **kwargs: (Path("node.exe"), Path("codegraph.js"))
            bad = clip_agent.handle_text(
                "CODE[K7Q2]: context -rf", runtime, clip_agent.real_execute, lambda *_: False,
            )
            self.assertIn("不能以 -", bad)
            no_index = clip_agent.handle_text(
                "CODE[K7Q2]: search hello", runtime, clip_agent.real_execute, lambda *_: False,
            )
            self.assertIn("建立代码索引", no_index)
            self.assertIn("OUTLINE", no_index)
            calls = []

            def fake_run(argv, cwd, timeout):
                calls.append(list(argv))
                return clip_agent.ProcResult(0, "符号 hello", "符号 hello", False)

            (root / ".codegraph").mkdir()
            clip_agent.run_codegraph = fake_run
            done = clip_agent.handle_text(
                "CODE[K7Q2]: search hello", runtime, clip_agent.real_execute, lambda *_: False,
            )
            self.assertIn("符号 hello", done)
            self.assertEqual(calls[0][calls[0].index("query") + 1], "hello")
        finally:
            clip_agent.find_codegraph = original_find
            clip_agent.run_codegraph = original_run

    def test_index_and_query_when_installed(self):
        if clip_agent.find_codegraph() is None:
            self.skipTest("未安装 CodeGraph")
        tmp = Path(tempfile.mkdtemp())
        (tmp / "app.py").write_text("def hello_clip():\n    return 1\n", encoding="utf-8")
        node, entry = clip_agent.find_codegraph()
        indexed = clip_agent.run_codegraph(clip_agent.codegraph_index_argv(node, entry, tmp, False), tmp, 90)
        self.assertEqual(indexed.exit_code, 0, indexed.text[-400:])
        self.assertTrue((tmp / ".codegraph").is_dir())
        found = clip_agent.run_codegraph(clip_agent.codegraph_argv(node, entry, "search", "hello_clip", tmp), tmp, 30)
        self.assertIn("hello_clip", found.text)


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.runtime = make_runtime(self.root)
        self.runtime.part_chars = 280
        self.runtime.max_chars = 500
        self.original_root = clip_agent.UPLOAD_ROOT
        clip_agent.UPLOAD_ROOT = self.root / "upload"

    def tearDown(self):
        clip_agent.UPLOAD_ROOT = self.original_root
        self.tmp.cleanup()

    def test_parts_rebuild_the_document_and_catalog_lists_them(self):
        chapters = []
        for index in range(1, 5):
            chapters.append("# 第%d章 标题%d\n\n%s" % (index, index, "正文%d" % index + "甲" * 80))
        source = "\n\n".join(chapters) + "\n电话 13800138000"
        (self.root / "合同.md").write_text(source, encoding="utf-8")
        self.assertEqual("\n".join(clip_agent.split_document(source, 280)), source)
        pack = clip_agent.build_upload_pack("审查付款", [self.root / "合同.md"], False, self.runtime)
        self.assertEqual("\n".join(part.body for part in pack.parts), source)
        titles = []
        for part in pack.parts:
            titles.extend(info[1] for line in part.body.splitlines() if (info := clip_agent.heading_info(line)))
        self.assertEqual(titles.count("第1章 标题1"), 1)
        self.assertEqual(titles.count("第4章 标题4"), 1)
        catalog = (pack.folder / "00_目录.md").read_text(encoding="utf-8")
        for part in pack.parts:
            self.assertIn(part.name, catalog)
            written = (pack.folder / part.name).read_text(encoding="utf-8")
            self.assertNotIn("13800138000", written)
        self.assertIn("脱敏", catalog)
        self.assertIn("00_目录.md", pack.message)

    def test_marker_file_is_not_exported_and_session_cleans_up(self):
        (self.root / "密.md").write_text("TOP SECRET\n不能导出的正文\n", encoding="utf-8")
        self.runtime.markers = ["TOP SECRET"]
        pack = clip_agent.build_upload_pack("看资料", [self.root / "密.md"], False, self.runtime)
        self.assertEqual(pack.parts, [])
        self.assertIn("密级", pack.message)
        self.assertFalse(any("不能导出" in path.read_text(encoding="utf-8") for path in pack.folder.glob("*.md")))
        folder = pack.folder
        self.assertTrue(folder.is_dir())
        clip_agent.close_session(self.runtime, "closed")
        self.assertFalse(folder.exists())
        leftover = clip_agent.upload_root() / "OLD"
        leftover.mkdir(parents=True)
        (leftover / "残留.md").write_text("x", encoding="utf-8")
        clip_agent.clear_uploads()
        self.assertFalse(leftover.exists())

    def test_short_task_stays_inline_and_attached_file_can_be_read(self):
        (self.root / "短.txt").write_text("只有一句。", encoding="utf-8")
        self.runtime.max_chars = 4000
        message = clip_agent.compose_task("看这句", [self.root / "短.txt"], False, self.runtime)
        self.assertIn("只有一句", message)
        self.assertFalse(clip_agent.session_upload(self.runtime.session).exists())
        outside = self.root.parent / "外面.txt"
        outside.write_text("外部内容", encoding="utf-8")
        self.runtime.attached.add(outside.resolve())
        rendered = clip_agent.handle_text(
            "READ_FILE[K7Q2]: %s" % outside,
            self.runtime, clip_agent.real_execute, lambda *_: False,
        )
        self.assertIn("外部内容", rendered)
        other = self.root.parent / "别的.txt"
        other.write_text("不该读到", encoding="utf-8")
        blocked = clip_agent.handle_text(
            "READ_FILE[K7Q2]: %s" % other,
            self.runtime, clip_agent.real_execute, lambda *_: False,
        )
        self.assertNotIn("不该读到", blocked)


class FreedomTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.runtime = make_runtime(self.root, mode="tiered")
        self.roots = (clip_agent.UPLOAD_ROOT, clip_agent.BACKUP_ROOT, clip_agent.SESSIONS_ROOT)
        clip_agent.UPLOAD_ROOT = self.root / "upload"
        clip_agent.BACKUP_ROOT = self.root / "backup"
        clip_agent.SESSIONS_ROOT = self.root / "sessions"

    def tearDown(self):
        clip_agent.UPLOAD_ROOT, clip_agent.BACKUP_ROOT, clip_agent.SESSIONS_ROOT = self.roots
        self.tmp.cleanup()

    def test_auto_receive_only_accepts_this_session(self):
        self.assertTrue(clip_agent.looks_like_directives("READ_FILE[K7Q2]: a.txt", "K7Q2"))
        self.assertFalse(clip_agent.looks_like_directives("hello", "K7Q2"))
        self.assertFalse(clip_agent.looks_like_directives("READ_FILE[ZZZZ]: a.txt", "K7Q2"))
        self.assertFalse(clip_agent.looks_like_directives("x" * 20001, "K7Q2"))

    def test_long_result_is_a_file_unless_marked(self):
        self.runtime.max_chars = 100
        name = clip_agent.spill_result(self.runtime, 3, "甲" * 180)
        self.assertEqual(name, "结果_第3步.md")
        self.assertEqual((clip_agent.session_upload(self.runtime.session) / name).read_text(encoding="utf-8"), "甲" * 180)
        self.runtime.markers = ["机密"]
        self.assertIsNone(clip_agent.spill_result(self.runtime, 4, "机密" + "甲" * 180))

    def test_patch_replaces_once_and_can_undo(self):
        target = self.root / "app.py"
        target.write_bytes(b"\xef\xbb\xbf" + "value = total\r\n".encode("utf-8"))
        calls = []

        def confirm(command, reason):
            calls.append(command)
            return True

        block = "\n".join([
            "PATCH[K7Q2]: app.py",
            "<<<<<<< 查找",
            "value = total",
            "=======",
            "value = round(total, 2)",
            ">>>>>>> 替换",
            "END_PATCH",
        ])
        missing = block.replace("value = total", "missing")
        denied = clip_agent.handle_text(missing, self.runtime, clip_agent.real_execute, confirm)
        self.assertIn("出现 0 次", denied)
        self.assertIn(b"value = total", target.read_bytes())
        twice = (self.root / "app.py").read_text(encoding="utf-8")
        target.write_text(twice + twice, encoding="utf-8")
        twice_denied = clip_agent.handle_text(block, self.runtime, clip_agent.real_execute, confirm)
        self.assertIn("出现 2 次", twice_denied)
        target.write_bytes(b"\xef\xbb\xbf" + "value = total\r\n".encode("utf-8"))
        self.runtime.check_command = "Write-Output CHECK_OK"
        done = clip_agent.handle_text(block, self.runtime, clip_agent.real_execute, confirm)
        self.assertIn("已修改", done)
        self.assertIn("检查通过", done)
        self.assertIn("CHECK_OK", done)
        data = target.read_bytes()
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"\r\n", data)
        self.assertIn("round(total, 2)", data.decode("utf-8-sig"))
        self.assertIn("已撤销", clip_agent.undo_last_change(self.runtime))
        self.assertIn("value = total", target.read_text(encoding="utf-8-sig"))
        target.write_text("changed again\n", encoding="utf-8")
        self.runtime.changed.append(str(target))
        # 再改一次后不能撤销上一份已经恢复的备份；重新打补丁后再改文件
        clip_agent.handle_text(block.replace("value = total", "changed again"), self.runtime, clip_agent.real_execute, confirm)
        target.write_text("later\n", encoding="utf-8")
        self.assertIn("又被改过", clip_agent.undo_last_change(self.runtime))
        readonly = make_runtime(self.root, mode="readonly")
        blocked = clip_agent.handle_text(block, readonly, clip_agent.real_execute, confirm)
        self.assertIn("只读档不能修改", blocked)

    def test_trust_is_exact_and_not_for_deletes(self):
        self.assertFalse(clip_agent.trust_allowed("Remove-Item a.txt"))
        self.assertTrue(clip_agent.trust_allowed("Write-Output hello"))
        asks = []

        def confirm(command, reason):
            asks.append(command)
            return True

        command = "TOOL[K7Q2]: Write-Output hello"
        clip_agent.handle_text(command, self.runtime, clip_agent.real_execute, confirm)
        self.assertEqual(len(asks), 1)
        self.runtime.trusted.add("Write-Output hello")
        clip_agent.handle_text(command, self.runtime, clip_agent.real_execute, confirm)
        self.assertEqual(len(asks), 1)
        clip_agent.handle_text("TOOL[K7Q2]: Write-Output hello!", self.runtime, clip_agent.real_execute, confirm)
        self.assertEqual(len(asks), 2)

    def test_note_is_redacted_and_resume_mentions_it(self):
        reply = clip_agent.handle_text(
            "NOTE[K7Q2]: 联系 alice@example.com",
            self.runtime, clip_agent.real_execute, lambda *_: False,
        )
        self.assertIn("已记下第 1 条", reply)
        notes = list(clip_agent.sessions_root().rglob("笔记.md"))[0].read_text(encoding="utf-8")
        self.assertNotIn("alice@example.com", notes)
        self.runtime.task_text = "审查合同"
        self.runtime.seen.append("合同.md")
        text = clip_agent.build_resume(self.runtime)
        self.assertIn("审查合同", text)
        self.assertIn("合同.md", text)
        self.runtime.max_chars = 20
        short = clip_agent.build_resume(self.runtime)
        self.assertIn("续接包.md", short)
        self.assertTrue((clip_agent.session_upload(self.runtime.session) / "续接包.md").is_file())


class DocTests(unittest.TestCase):
    def test_prompt_file_matches_builtin_template(self):
        text = Path(__file__).with_name("COPILOT_SYSTEM_PROMPT.md").read_text(encoding="utf-8")
        self.assertIn(clip_agent.SYSTEM_PROMPT.strip(), text)
        self.assertIn("{{SESSION}}", text)
        self.assertIn("COPILOT_BRIEFING.md", text)

    def test_brief_contains_runtime_facts_and_exact_grammar(self):
        text = clip_agent.build_copilot_brief(
            "K7Q2", "readonly", Path(r"D:\work"), [Path(r"D:\work")], 5, 60, 8000, [],
        )
        self.assertIn("会话码：`K7Q2`", text)
        self.assertIn(r"D:\work", text)
        self.assertIn("TOOL[K7Q2]: Get-ChildItem -Name", text)
        self.assertIn("READ_FILE[K7Q2]: result:1#L81-160", text)
        self.assertIn("`get-childitem`", text)
        self.assertIn("`status`", text)
        self.assertIn("[RESULT #编号]", text)
        self.assertIn("BLOCKED", text)
        self.assertNotIn("{{SESSION}}", text)

    def test_brief_sets_reply_format_and_its_examples_parse(self):
        root = Path(tempfile.gettempdir()).resolve()
        briefs = {
            mode: clip_agent.build_copilot_brief(SESSION, mode, root, [root], 5, 60, 8000, [], ["pandas"])
            for mode in ("readonly", "tiered")
        }
        for mode, text in briefs.items():
            with self.subTest(mode=mode):
                self.assertIn("已就绪，请告诉我任务", text)
                self.assertIn("DONE:", text)
                self.assertIn("OUTLINE[K7Q2]: .", text)
                self.assertLess(len(text), 4000)
                blocks = [
                    block for block in re.findall(r"```text\n(.*?)```", text, re.S)
                    if "BEGIN OUTPUT" not in block
                ]
                self.assertTrue(blocks)
                for block in blocks:
                    directives, error = clip_agent.parse_batch(block, SESSION, 5)
                    self.assertIsNone(error, block)
                    self.assertTrue(directives, block)
        self.assertIn("PYTHON[K7Q2]:", briefs["tiered"])
        self.assertIn("END_PYTHON", briefs["tiered"])
        self.assertIn("pandas", briefs["tiered"])
        self.assertNotIn("PYTHON[K7Q2]:", briefs["readonly"])


if __name__ == "__main__":
    unittest.main()
