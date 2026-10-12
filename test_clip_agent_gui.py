"""窗口版的会话流程。不测浏览器，只驱动窗口控件。"""

import os
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

import tkinter as tk
from tkinter import ttk

import clip_agent as core
import clip_agent_gui as gui
from test_clip_agent import read_clipboard_text


class TemplateTests(unittest.TestCase):
    def test_templates_follow_mode(self):
        readonly = gui.task_templates("readonly")["分析表格"]
        tiered = gui.task_templates("tiered")["分析表格"]
        self.assertIn("READ_FILE", readonly)
        self.assertNotIn("Python", readonly)
        self.assertIn("Python", tiered)
        auto = gui.task_templates("auto")["分析表格"]
        self.assertIn("Python", auto)

    def test_batch_summary_counts_decisions(self):
        items = [
            core.Item(1, "TOOL pwd", "0", 0.1, "ok", 0, True, "allow"),
            core.Item(2, "BLOCKED", "-", 0, "未执行", 0, False, "block"),
        ]
        self.assertEqual(gui.batch_summary(items), "执行 1 条，拦截 1 条")


class ConfirmTests(unittest.TestCase):
    def test_escape_rejects_and_run_starts_disabled(self):
        root = tk.Tk()
        root.geometry("900x640+60+60")
        root.update()

        class Probe(gui.ConfirmDialog):
            def present(self):
                self.focus_deny()
                path = self.top.tk.eval("focus")
                focused = self.top.nametowidget(path) if path else None
                self.focus_text = focused.cget("text") if isinstance(focused, ttk.Button) else ""
                self.run_state = str(self.run_button.cget("state"))
                self.assert_viewable = self.deny.winfo_viewable() and self.run_button.winfo_viewable()
                self.top.event_generate("<Escape>")
                if self.top.winfo_exists():
                    self.reject()

        dialog = Probe(root, "PYTHON[K7Q2]:\nprint(1)\nEND_PYTHON", "运行 Python 代码")
        root.destroy()
        self.assertFalse(dialog.ok)
        self.assertEqual(dialog.focus_text, "拒绝")
        self.assertEqual(dialog.run_state, "disabled")
        self.assertTrue(dialog.assert_viewable)


class WindowFlowTests(unittest.TestCase):
    def setUp(self):
        self.previous = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name).resolve()
        self.logs = self.project / "logs"
        self.backups = []
        for path in (core.SAVED_WORKDIR, core.TOOL_DIR / "COPILOT_BRIEFING.md"):
            self.backups.append((path, path.read_bytes() if path.exists() else None))
        self.tk = tk.Tk()
        self.tk.geometry("980x820+40+40")
        args = core.build_parser().parse_args([
            "--workdir", str(self.project),
            "--mode", "readonly",
            "--log-dir", str(self.logs),
            "--timeout", "20",
            "--clear-after", "600",
        ])
        self.app = gui.App(self.tk, args)
        self.errors = []
        self.tk.report_callback_exception = lambda *info: self.errors.append(info)
        self.tk.update()

    def tearDown(self):
        try:
            if self.app.rt is not None:
                self.app.end_session("test")
        finally:
            self.tk.destroy()
            os.chdir(self.previous)
            for path, data in self.backups:
                if data is None:
                    if path.exists():
                        path.unlink()
                else:
                    path.write_bytes(data)
            self.tmp.cleanup()

    def pump(self, ready, seconds: float = 20) -> bool:
        end = time.time() + seconds
        while time.time() < end:
            self.tk.update()
            if ready():
                return True
            time.sleep(0.02)
        return False

    def test_paste_runs_readonly_command_and_copies_result(self):
        self.app.start_session()
        self.tk.update()
        runtime = self.app.rt
        self.assertIsNotNone(runtime)
        self.assertEqual(runtime.input_method, "window")
        self.assertIn(runtime.session, self.tk.title())
        self.assertIn("已就绪，请告诉我任务", read_clipboard_text())
        startup = self.logs / ("audit-%s.jsonl" % date.today().strftime("%Y%m%d"))
        self.assertIn(core.sha256_file(gui.FRONT_END), startup.read_text(encoding="utf-8"))

        core.clipboard_write("TOOL[%s]: Get-Location" % runtime.session)
        self.app.input.focus_set()
        self.app.input.event_generate("<<Paste>>")
        ready = self.pump(lambda: "RESULT" in self.app.result.get("1.0", "end"))
        self.assertFalse(self.errors)
        self.assertTrue(ready, self.app.status.get())
        self.assertIn("exit=0", self.app.result.get("1.0", "end"))
        copied = read_clipboard_text()
        self.assertIn("[RESULT #1]", copied)
        self.assertIn("结果已复制", self.app.status.get())

        before = read_clipboard_text()
        self.app.input.delete("1.0", "end")
        self.app.input.insert("1.0", runtime.brief)
        self.app.run_input()
        self.assertTrue(self.pump(lambda: "应该贴到 Copilot" in self.app.status.get()), self.app.status.get())
        self.assertEqual(read_clipboard_text(), before)

        self.app.input.delete("1.0", "end")
        self.app.input.insert("1.0", "TOOL[ZZZZ]: Get-Location")
        self.app.run_input()
        self.assertTrue(self.pump(lambda: "[REJECTED]" in self.app.result.get("1.0", "end")))
        rejected = read_clipboard_text()
        self.assertIn("[REJECTED]", rejected)
        self.assertNotIn(runtime.session, rejected)
        self.assertEqual(core.verify_audit(startup), [])


if __name__ == "__main__":
    unittest.main()
