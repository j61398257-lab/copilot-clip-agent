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


class DocTests(unittest.TestCase):
    def test_prompt_file_matches_builtin_template(self):
        text = Path(__file__).with_name("COPILOT_SYSTEM_PROMPT.md").read_text(encoding="utf-8")
        self.assertIn(clip_agent.SYSTEM_PROMPT.strip(), text)
        self.assertIn("{{SESSION}}", text)


if __name__ == "__main__":
    unittest.main()
