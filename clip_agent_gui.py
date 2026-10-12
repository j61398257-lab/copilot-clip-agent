"""Copilot 执行器的窗口版。

不监听剪贴板：只有你在输入框里按 Ctrl+V（或右键“粘贴并执行”）时，输入框才读取一次剪贴板。
解析、策略、执行、脱敏和审计都走 clip_agent 里同一套代码。
代码索引建成后，用 codegraph_page 把索引写成一页本地 HTML，再交给系统打开。
"""

from __future__ import annotations

import copy
import ctypes
import json
import os
import queue
import threading
import time
import tkinter as tk
import traceback
import winsound
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import clip_agent as core
import codegraph_page

FRONT_END = Path(__file__).resolve()
UI_FONT = ("Microsoft YaHei UI", 10)
BOLD_FONT = ("Microsoft YaHei UI", 10, "bold")
TITLE_FONT = ("Microsoft YaHei UI", 15, "bold")
CODE_FONT = ("Consolas", 10)
MODE_NAMES = {"readonly": "只读严格", "tiered": "分级确认", "auto": "自动执行"}
TONES = {"ok": "#1d6b2f", "info": "#333333", "warn": "#9a5b00", "error": "#b3261e"}
DECISION_NAMES = {"allow": "执行", "block": "拦截", "marker": "拦截", "deny": "拒绝", "skip": "跳过"}


def task_templates(mode: str) -> dict[str, str]:
    if core.mode_can_change(mode):
        table = (
            "请用 Python 分析【表格路径，比如 samples\\sales.csv】：先看列名和前几行，"
            "再按类别汇总数量和金额，找出金额最高的几项，最后用几句话给出结论。"
        )
    else:
        table = (
            "请用 READ_FILE 读取【表格路径，比如 samples\\sales.csv】，按类别汇总数量和金额，"
            "找出金额最高的几项，最后用几句话给出结论。"
        )
    return {
        "了解这个项目": "请帮我了解这个项目：先看目录结构和 README，再告诉我它是做什么的、怎么运行、主要代码在哪里。",
        "分析表格": table,
        "查看最近的改动": "请看这个项目最近 10 次提交和当前未提交的改动，总结改了什么，指出可能的问题。",
        "找功能在哪里实现": "请帮我找到【把功能写在这里】是在哪个文件、哪个函数里实现的，并说明调用流程。",
        "排查报错": "我运行时遇到这个报错：【把报错贴在这里】。请在项目里找到相关代码，分析原因并给出修改建议，不要直接改文件。",
    }


def batch_summary(items: list[core.Item]) -> str:
    counts: dict[str, int] = {}
    for item in items:
        name = DECISION_NAMES.get(item.decision, item.decision)
        counts[name] = counts.get(name, 0) + 1
    return "，".join("%s %d 条" % (name, count) for name, count in counts.items())


def scrolled_text(parent, **options) -> tk.Text:
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True)
    text = tk.Text(frame, relief="solid", borderwidth=1, padx=6, pady=4, **options)
    vertical = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=vertical.set)
    text.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    if options.get("wrap") == "none":
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
        text.configure(xscrollcommand=horizontal.set)
        horizontal.grid(row=1, column=0, sticky="ew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    text.bind("<Control-a>", lambda _event: (text.tag_add("sel", "1.0", "end-1c"), "break")[1])
    return text


def auto_wrap(label: ttk.Label, width: int = 640) -> ttk.Label:
    label.configure(wraplength=width)
    label.bind("<Configure>", lambda event: label.configure(wraplength=max(200, event.width - 8)))
    return label


def set_text(widget: tk.Text, value: str) -> None:
    state = widget.cget("state")
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", value)
    widget.configure(state=state)


class ConfirmDialog:
    """弹出将要执行的完整内容，默认焦点在“拒绝”，“执行”按钮稍后才可点，避免顺手回车放行。"""

    def __init__(self, parent: tk.Tk, command: str, reason: str) -> None:
        self.ok = False
        self.trust = tk.BooleanVar(value=False)
        lines = command.splitlines()
        python = command.startswith("PYTHON[")
        top = self.top = tk.Toplevel(parent)
        top.title("确认运行 Python 代码（%d 行）" % max(0, len(lines) - 2) if python else "确认执行这条指令")
        top.transient(parent)
        top.minsize(600, 320)
        body = ttk.Frame(top, padding=14)
        body.pack(fill="both", expand=True)
        auto_wrap(ttk.Label(body, text="需要你确认：%s" % reason, font=BOLD_FONT)).pack(fill="x")
        auto_wrap(ttk.Label(
            body, text="下面是将要执行的完整内容，看清楚再决定。拒绝后，本批剩下的指令也不会执行。",
        )).pack(fill="x", pady=(4, 8))
        box = scrolled_text(body, height=min(24, max(6, len(lines))), width=90, wrap="none", font=CODE_FONT)
        box.insert("1.0", command)
        box.configure(state="disabled", takefocus=0)
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(10, 0))
        body_text = command.split("]: ", 1)[-1] if command.startswith("TOOL") else ""
        if body_text and core.trust_allowed(body_text) and not command.startswith(("PYTHON", "PATCH")):
            ttk.Checkbutton(buttons, text="本会话内这条命令不再询问", variable=self.trust).pack(side="left")
        self.run_button = ttk.Button(
            buttons, text="执行", command=self.accept, state="disabled", default="disabled",
        )
        self.run_button.pack(side="right")
        self.deny = ttk.Button(buttons, text="拒绝", command=self.reject, default="disabled")
        self.deny.pack(side="right", padx=8)
        top.protocol("WM_DELETE_WINDOW", self.reject)
        top.bind("<Escape>", lambda _event: self.reject())
        top.bind("<Return>", self.on_return)
        top.after(800, lambda: self.run_button.configure(state="normal"))
        top.update_idletasks()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - top.winfo_reqwidth()) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - top.winfo_reqheight()) // 3)
        top.geometry("+%d+%d" % (x, y))
        self.present()

    def on_return(self, event) -> str:
        if event.widget is self.run_button and str(self.run_button.cget("state")) != "disabled":
            self.accept()
        elif event.widget is self.deny:
            self.reject()
        return "break"

    def focus_deny(self) -> None:
        self.top.wait_visibility()
        self.top.lift()
        self.top.focus_force()
        self.deny.focus_set()
        self.top.update()

    def present(self) -> None:
        self.focus_deny()
        self.top.grab_set()
        self.top.wait_window(self.top)

    def accept(self) -> None:
        self.ok = True
        self.top.destroy()

    def reject(self) -> None:
        self.ok = False
        self.top.destroy()


class App:
    def __init__(self, root: tk.Tk, base) -> None:
        self.root = root
        self.base = base
        self.args = base
        self.rt: core.Runtime | None = None
        self.janitor: core.ClipboardJanitor | None = None
        self.busy = False
        self.last_payload = ""
        self.last_active = time.monotonic()
        self.inbox: queue.Queue = queue.Queue()
        self.templates: dict[str, str] = {}
        self.status = tk.StringVar()
        root.title("Copilot 执行器")
        scale = root.winfo_fpixels("1i") / 96.0
        width = min(int(940 * scale), root.winfo_screenwidth() - 40)
        height = min(int(860 * scale), root.winfo_screenheight() - 80)
        root.geometry("%dx%d" % (width, height))
        root.minsize(min(int(760 * scale), width), min(int(600 * scale), height))
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.report_callback_exception = self.on_error
        style = ttk.Style(root)
        style.configure(".", font=UI_FONT)
        style.configure("TLabelframe.Label", font=BOLD_FONT)
        self.build_setup()
        self.build_session()
        self.show(self.setup)
        root.after(50, self.poll)
        root.after(300, self.poll_auto)
        root.after(30000, self.check_idle)
        self.own_seq = None
        self.auto_seen = None

    def build_setup(self) -> None:
        frame = self.setup = ttk.Frame(self.root, padding=24)
        ttk.Label(frame, text="Copilot 执行器", font=TITLE_FONT).grid(row=0, column=0, columnspan=3, sticky="w")
        auto_wrap(ttk.Label(
            frame,
            text="把 Copilot 回复里的指令粘贴进来执行，结果自动复制回剪贴板，你再贴回 Copilot。"
            "不联网，不操作 Copilot 网页，不监听剪贴板。",
        )).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(6, 20))
        ttk.Label(frame, text="项目目录").grid(row=2, column=0, sticky="w")
        initial = self.base.workdir or core.load_saved_workdir(core.SAVED_WORKDIR) or ""
        self.workdir = tk.StringVar(value=initial)
        ttk.Entry(frame, textvariable=self.workdir, width=64).grid(row=2, column=1, sticky="ew", padx=8)
        ttk.Button(frame, text="选择…", command=self.pick_dir).grid(row=2, column=2)
        ttk.Label(frame, text="档位").grid(row=3, column=0, sticky="nw", pady=(16, 0))
        self.mode = tk.StringVar(value=self.base.mode)
        modes = ttk.Frame(frame)
        modes.grid(row=3, column=1, columnspan=2, sticky="w", pady=(16, 0))
        ttk.Radiobutton(
            modes, value="readonly", variable=self.mode,
            text="只读严格：只执行白名单里的只读命令和 READ_FILE，不能运行 Python",
        ).pack(anchor="w")
        ttk.Radiobutton(
            modes, value="tiered", variable=self.mode,
            text="分级确认：白名单外的命令和 Python 代码逐条弹窗，你点“执行”才运行",
        ).pack(anchor="w", pady=(6, 0))
        ttk.Radiobutton(
            modes, value="auto", variable=self.mode,
            text="自动执行：普通命令、Python 和 PATCH 直接运行，不再逐条确认；高风险操作仍然拒绝",
        ).pack(anchor="w", pady=(6, 0))
        ttk.Label(frame, text="单条上限").grid(row=4, column=0, sticky="w", pady=(16, 0))
        saved = load_settings()
        self.max_chars = tk.StringVar(value=str(saved.get("max_chars", self.base.max_chars)))
        ttk.Combobox(frame, textvariable=self.max_chars, values=("4000", "8000", "16000", "32000"), width=10).grid(
            row=4, column=1, sticky="w", pady=(16, 0),
        )
        ttk.Button(frame, text="测上限", command=self.copy_limit_probe).grid(row=4, column=2, sticky="w", pady=(16, 0))
        ttk.Button(frame, text="开始会话", command=self.start_session).grid(row=5, column=1, sticky="w", pady=(24, 0))
        self.setup_status = auto_wrap(ttk.Label(frame, textvariable=self.status))
        self.setup_status.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(18, 0))
        frame.columnconfigure(1, weight=1)

    def build_session(self) -> None:
        frame = self.session = ttk.Frame(self.root, padding=12)
        head = ttk.Frame(frame)
        head.pack(fill="x")
        self.header = tk.StringVar()
        ttk.Label(head, textvariable=self.header, font=BOLD_FONT).pack(side="left")
        ttk.Button(head, text="结束会话", command=self.end_session).pack(side="right")

        step1 = ttk.LabelFrame(frame, text="① 把预设提示词贴给 Copilot", padding=8)
        step1.pack(fill="x", pady=(10, 0))
        ttk.Button(step1, text="复制预设提示词", command=self.copy_brief).pack(side="left")
        auto_wrap(ttk.Label(
            step1, text="开始会话时已经自动复制。到 Copilot 对话框粘贴并发送，它会回复“已就绪，请告诉我任务”。",
        )).pack(side="left", fill="x", expand=True, padx=10)

        step2 = ttk.LabelFrame(frame, text="② 告诉 Copilot 要做什么（可以选模板，改好后复制，贴给 Copilot）", padding=8)
        step2.pack(fill="x", pady=(10, 0))
        row = ttk.Frame(step2)
        row.pack(fill="x")
        self.template = tk.StringVar()
        self.template_box = ttk.Combobox(row, textvariable=self.template, state="readonly", width=22)
        self.template_box.pack(side="left")
        self.template_box.bind("<<ComboboxSelected>>", lambda _event: self.fill_template())
        ttk.Button(row, text="复制任务", command=self.copy_task).pack(side="left", padx=8)
        ttk.Button(row, text="添加文件…", command=self.add_files).pack(side="left")
        ttk.Button(row, text="打开上传包", command=self.open_pack).pack(side="left", padx=8)
        self.with_code = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="附带代码上下文", variable=self.with_code).pack(side="left")
        self.files: list[Path] = []
        self.file_box = tk.Listbox(step2, height=3)
        self.file_box.pack(fill="x", pady=(6, 0))
        task_frame = ttk.Frame(step2)
        task_frame.pack(fill="x", pady=(6, 0))
        self.task = scrolled_text(task_frame, height=3, wrap="word", font=UI_FONT, undo=True)
        self.task.bind("<<Paste>>", self.on_task_paste)

        self.step3 = ttk.LabelFrame(frame, padding=8)
        self.step3.pack(fill="both", expand=True, pady=(10, 0))
        self.input = scrolled_text(self.step3, height=8, wrap="none", font=CODE_FONT, undo=True)
        self.input.bind("<<Paste>>", self.on_paste)
        self.input.bind("<Control-Return>", lambda _event: (self.run_input(), "break")[1])
        self.input.bind("<Button-3>", self.input_menu)
        buttons = ttk.Frame(self.step3)
        buttons.pack(fill="x", pady=(6, 0))
        self.auto = tk.BooleanVar(value=False)
        ttk.Checkbutton(buttons, text="自动接收", variable=self.auto, command=self.arm_auto).pack(side="left", padx=(0, 8))
        self.run_button = ttk.Button(buttons, text="执行（Ctrl+Enter）", command=self.run_input)
        self.run_button.pack(side="left")
        ttk.Button(buttons, text="清空", command=self.clear_input).pack(side="left", padx=8)
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="粘贴并执行", command=lambda: self.input.event_generate("<<Paste>>"))
        self.menu.add_command(label="清空", command=self.clear_input)

        self.step4 = ttk.LabelFrame(frame, padding=8)
        self.step4.pack(fill="both", expand=True, pady=(10, 0))
        self.result = scrolled_text(self.step4, height=10, wrap="word", font=CODE_FONT, state="disabled")
        extra = ttk.Frame(self.step4)
        extra.pack(fill="x", pady=(6, 0))
        ttk.Button(extra, text="再次复制结果", command=self.copy_result).pack(side="left")
        ttk.Button(extra, text="撤销上一次修改", command=self.undo_change).pack(side="left", padx=8)
        ttk.Button(extra, text="生成续接包", command=self.copy_resume).pack(side="left")
        ttk.Button(extra, text="建立/更新代码索引", command=self.start_index).pack(side="left", padx=8)
        ttk.Button(extra, text="打开代码地图", command=self.open_map).pack(side="left")
        self.index_status = tk.StringVar(value="")
        ttk.Label(extra, textvariable=self.index_status).pack(side="left")

        self.session_status = auto_wrap(ttk.Label(frame, textvariable=self.status))
        self.session_status.pack(fill="x", pady=(10, 0))

    def show(self, frame: ttk.Frame) -> None:
        for other in (self.setup, self.session):
            other.pack_forget()
        frame.pack(fill="both", expand=True)

    def say(self, text: str, tone: str = "info") -> None:
        self.status.set(text)
        for label in (self.setup_status, self.session_status):
            label.configure(foreground=TONES.get(tone, TONES["info"]))

    def pick_dir(self) -> None:
        chosen = filedialog.askdirectory(
            parent=self.root, title="选择项目目录", mustexist=True,
            initialdir=self.workdir.get() or None,
        )
        if chosen:
            self.workdir.set(str(Path(chosen)))

    def start_session(self) -> None:
        workdir = self.workdir.get().strip().strip('"').strip()
        if not workdir:
            self.say("先选择项目目录。", "warn")
            return
        args = copy.copy(self.base)
        args.workdir = workdir
        args.mode = self.mode.get()
        try:
            args.max_chars = int(self.max_chars.get())
        except ValueError:
            self.say("单条上限要是数字。", "warn")
            return
        save_settings({"max_chars": args.max_chars})
        roots, problem = core.collect_roots(args)
        if problem:
            self.say(problem, "error")
            return
        if args.mode == "tiered" and not messagebox.askyesno(
            "确认使用分级档",
            "分级确认档会把白名单之外的命令和 Python 代码逐条弹窗给你看，你点“执行”才会运行。\n\n确定使用分级档吗？",
            icon="warning", default="no", parent=self.root,
        ):
            return
        if args.mode == "auto" and not messagebox.askyesno(
            "确认使用自动执行",
            "自动执行档会直接运行白名单外的普通命令、Python 代码和 PATCH，不再逐条确认。\n\n"
            "下载、编码执行、递归删除、关机等高风险操作仍然直接拒绝。允许目录以外的读取也不会自动放行。\n\n"
            "确定使用自动执行档吗？",
            icon="warning", default="no", parent=self.root,
        ):
            return
        try:
            audit = core.AuditLog(Path(args.log_dir), args.retention_days)
        except OSError as exc:
            self.say("无法写入审计日志：%s" % exc, "error")
            return
        self.say("正在准备会话……")
        self.root.update_idletasks()
        rt, _script_hash = core.open_session(args, roots, audit, "window", FRONT_END)
        self.rt = rt
        self.args = args
        for target, text in ((core.TOOL_DIR / "COPILOT_BRIEFING.md", rt.brief), (core.SAVED_WORKDIR, str(roots[0]) + "\n")):
            try:
                target.write_text(text, encoding="utf-8")
            except OSError:
                pass
        self.janitor = core.ClipboardJanitor()
        self.janitor.start()
        self.root.title("Copilot 执行器 [%s] %s" % (core.mode_short(args.mode), rt.session))
        self.header.set("会话码 %s　·　%s　·　%s" % (rt.session, MODE_NAMES[args.mode], rt.cwd))
        self.step3.configure(text="③ 粘贴 Copilot 回复里的代码块（Ctrl+V 粘贴后自动执行，也可以点“执行”）")
        self.step4.configure(
            text="④ 结果已复制到剪贴板，回 Copilot 粘贴发送（%g 秒后若剪贴板没变会自动清空）" % args.clear_after
        )
        self.templates = task_templates(args.mode)
        self.template_box["values"] = list(self.templates)
        self.template_box.current(0)
        self.fill_template()
        self.clear_input()
        set_text(self.result, "")
        self.last_payload = ""
        self.last_active = time.monotonic()
        self.show(self.session)
        try:
            core.clipboard_write(rt.brief)
        except OSError as exc:
            self.say("会话已开始，但复制预设提示词失败：%s。请点“复制预设提示词”重试。" % exc, "error")
            return
        started = "会话已开始，预设提示词已复制到剪贴板。到 Copilot 对话框粘贴并发送。"
        if rt.python_note:
            self.say(started + "\n" + rt.python_note, "warn")
        else:
            self.say(started, "ok")

    def end_session(self, reason: str = "closed") -> bool:
        if self.rt is None:
            return True
        if self.busy:
            self.say("正在执行，等这一批结束后再结束会话。", "warn")
            return False
        core.close_session(self.rt, reason)
        if self.janitor is not None:
            self.janitor.stop()
        self.rt = None
        self.janitor = None
        self.root.title("Copilot 执行器")
        self.show(self.setup)
        self.say("会话已结束。重新开始会话后，要把新的预设提示词贴给 Copilot。")
        return True

    def fill_template(self) -> None:
        set_text(self.task, self.templates.get(self.template.get(), ""))

    def copy_text(self, text: str, done: str) -> int | None:
        try:
            seq = core.clipboard_write(text)
        except OSError as exc:
            self.say("复制失败：%s" % exc, "error")
            return None
        self.last_active = time.monotonic()
        self.say(done, "ok")
        return seq

    def copy_brief(self) -> None:
        if self.rt is not None:
            self.copy_text(self.rt.brief, "预设提示词已复制。到 Copilot 对话框粘贴并发送。")

    def add_files(self) -> None:
        chosen = filedialog.askopenfilenames(parent=self.root, title="添加文件")
        for name in chosen:
            self.add_attachment(Path(name))

    def add_attachment(self, path: Path) -> None:
        if len(self.files) >= 5:
            self.say("一次最多附加 5 个文件。", "warn")
            return
        resolved = path.resolve()
        if resolved not in self.files:
            self.files.append(resolved)
            self.file_box.insert("end", resolved.name)

    def on_task_paste(self, _event=None):
        files = clipboard_files()
        if not files:
            return None
        for name in files:
            self.add_attachment(Path(name))
        return "break"

    def copy_task(self) -> None:
        if self.rt is None:
            return
        text = self.task.get("1.0", "end-1c").strip()
        if not text:
            self.say("任务是空的。先选一个模板或自己写一句。", "warn")
            return
        self.rt.max_chars = int(self.max_chars.get())
        files = list(self.files)
        with_code = bool(self.with_code.get())
        self.say("正在准备任务……")
        threading.Thread(target=self.compose_worker, args=(text, files, with_code), daemon=True).start()

    def compose_worker(self, text: str, files: list, with_code: bool) -> None:
        try:
            message = core.compose_task(text, files, with_code, self.rt)
        except Exception as exc:
            self.inbox.put(("error", "%s：%s" % (exc.__class__.__name__, exc)))
            return
        self.inbox.put(("task", message))

    def open_pack(self) -> None:
        if self.rt is None:
            return
        folder = core.session_upload(self.rt.session)
        if not folder.is_dir():
            self.say("还没有上传包。", "warn")
            return
        os.startfile(folder)

    def copy_limit_probe(self) -> None:
        try:
            limit = int(self.max_chars.get())
        except ValueError:
            self.say("单条上限要是数字。", "warn")
            return
        marker = "【结尾标记 %d】" % limit
        body = ("0123456789" * ((limit // 10) + 1))[: max(0, limit - len(marker))]
        self.copy_text(body + marker, "测试文字已复制（%d 字符）。粘进 Copilot 输入框，不用发送，看结尾标记还在不在。" % limit)

    def arm_auto(self) -> None:
        if self.auto.get():
            self.auto_seen = core.clipboard_sequence()
            self.last_active = time.monotonic()
            self.say("自动接收已打开。只读指令会直接执行。要运行或要修改的指令仍请粘贴到第 ③ 步。", "ok")

    def undo_change(self) -> None:
        if self.rt is None:
            return
        self.say(core.undo_last_change(self.rt), "ok")

    def copy_resume(self) -> None:
        if self.rt is None:
            return
        self.copy_text(core.build_resume(self.rt), "续接包已复制。")

    def start_index(self) -> None:
        if self.rt is None:
            return
        self.index_status.set("正在索引……")
        threading.Thread(target=self.index_worker, daemon=True).start()

    def index_worker(self) -> None:
        pair = core.find_codegraph()
        if pair is None:
            self.inbox.put(("status", "未安装 CodeGraph，使用内置概览"))
            return
        indexed = (self.rt.cwd / ".codegraph").is_dir()
        proc = core.run_codegraph(core.codegraph_index_argv(pair[0], pair[1], self.rt.cwd, indexed), self.rt.cwd, 180)
        if proc.exit_code != 0:
            self.inbox.put(("status", "索引失败"))
            return
        self.publish_map("索引已更新，代码地图已在浏览器打开。")

    def open_map(self) -> None:
        if self.rt is None:
            return
        self.index_status.set("正在生成代码地图……")
        threading.Thread(target=self.map_worker, daemon=True).start()

    def map_worker(self) -> None:
        self.publish_map("代码地图已在浏览器打开。")

    def publish_map(self, prefix: str) -> None:
        if self.rt is None:
            return
        try:
            page = codegraph_page.write_page(
                self.rt.cwd,
                hidden=lambda path: core.path_is_sensitive(Path(path)),
                redact_text=lambda text: core.redact(text)[0],
            )
        except codegraph_page.MapError as exc:
            self.inbox.put(("status", "代码地图没有生成：%s" % exc, "地图未生成", "warn"))
            return
        self.inbox.put(("map", str(page), prefix))

    def show_map(self, path: str, prefix: str) -> None:
        self.index_status.set("代码地图已打开")
        try:
            codegraph_page.open_local_html(Path(path))
        except OSError as exc:
            self.say("%s打开失败：%s。页面文件在 %s" % (prefix, exc, path), "warn")
            return
        self.say(
            "%s用 Edge 打开这一页后，可用侧边栏 Copilot 阅读，并核对页末的【代码地图结尾】。页面文件在 %s"
            % (prefix, path),
            "ok",
        )

    def copy_result(self) -> None:
        if not self.last_payload:
            self.say("还没有结果可以复制。", "warn")
            return
        seq = self.copy_text(self.last_payload, "结果已重新复制（%d 字符）。回到 Copilot 粘贴并发送。" % len(self.last_payload))
        if seq is not None and self.janitor is not None:
            self.janitor.track(seq, self.args.clear_after)

    def clear_input(self) -> None:
        if not self.busy:
            self.input.delete("1.0", "end")

    def input_menu(self, event) -> None:
        self.menu.tk_popup(event.x_root, event.y_root)

    def on_paste(self, _event=None):
        if self.busy or self.rt is None:
            return "break"
        self.input.delete("1.0", "end")
        self.root.after_idle(self.run_input)
        return None

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.run_button.configure(state="disabled" if busy else "normal")
        self.input.configure(state="disabled" if busy else "normal")

    def run_input(self, method: str = "window") -> None:
        if self.busy or self.rt is None:
            return
        self.rt.input_method = method
        text = self.input.get("1.0", "end-1c")
        if not text.strip():
            self.say("输入框是空的。把 Copilot 回复里的代码块复制过来，在这里按 Ctrl+V。", "warn")
            return
        self.last_active = time.monotonic()
        self.set_busy(True)
        self.say("正在执行……")
        threading.Thread(target=self.work, args=(self.rt, text), daemon=True).start()

    def work(self, rt: core.Runtime, text: str) -> None:
        try:
            reply = core.respond(text, rt, core.real_execute, self.confirm_from_worker)
        except Exception as exc:
            self.inbox.put(("error", "%s：%s" % (exc.__class__.__name__, exc)))
            return
        self.inbox.put(("reply", reply))

    def confirm_from_worker(self, command: str, reason: str) -> bool:
        holder = {"ok": False}
        done = threading.Event()
        self.inbox.put(("confirm", command, reason, holder, done))
        done.wait()
        return holder["ok"]

    def poll(self) -> None:
        try:
            while True:
                try:
                    message = self.inbox.get_nowait()
                except queue.Empty:
                    break
                self.handle(message)
        finally:
            self.root.after(50, self.poll)

    def handle(self, message: tuple) -> None:
        kind = message[0]
        if kind == "confirm":
            _, command, reason, holder, done = message
            try:
                dialog = ConfirmDialog(self.root, command, reason)
                holder["ok"] = dialog.ok
                body = command.split("]: ", 1)[-1] if command.startswith("TOOL") else ""
                if dialog.ok and dialog.trust.get() and body and self.rt is not None and core.trust_allowed(body):
                    self.rt.trusted.add(body)
            finally:
                done.set()
        elif kind == "reply":
            self.set_busy(False)
            self.show_reply(message[1])
        elif kind == "task":
            self.deliver_task(message[1])
        elif kind == "status":
            text = message[1]
            short = message[2] if len(message) > 2 else text
            tone = message[3] if len(message) > 3 else ("error" if "失败" in text else "ok")
            self.index_status.set(short)
            self.say(text, tone)
        elif kind == "map":
            self.show_map(message[1], message[2])
        elif kind == "error":
            self.set_busy(False)
            self.say("执行出错：%s" % message[1], "error")

    def show_reply(self, reply: core.Reply) -> None:
        self.last_active = time.monotonic()
        if reply.payload is None:
            self.say("\n".join(reply.notes), "warn")
            return
        payload = reply.payload
        if self.rt is not None:
            spilled = core.spill_result(self.rt, self.rt.step, payload)
            if spilled:
                payload = "完整结果在 %s（%d 字），请上传后继续。\n\n%s" % (spilled, len(reply.payload), reply.payload[:800])
                self.open_pack()
        self.last_payload = payload
        set_text(self.result, payload)
        try:
            seq = core.clipboard_write(payload)
            self.own_seq = seq
        except OSError as exc:
            self.say("写回剪贴板失败：%s。可以点“再次复制结果”重试。" % exc, "error")
            return
        if self.janitor is not None:
            self.janitor.track(seq, self.args.clear_after)
        try:
            winsound.MessageBeep(winsound.MB_OK)
        except RuntimeError:
            pass
        parts = ["结果已复制到剪贴板（%d 字符），回到 Copilot 粘贴并发送。" % len(reply.payload)]
        if reply.items:
            parts.append("本批：%s。" % batch_summary(reply.items))
        parts.extend(reply.notes)
        self.say("\n".join(parts), "warn" if reply.kind == "rejected" else "ok")

    def deliver_task(self, message: str) -> None:
        folder = core.session_upload(self.rt.session) if self.rt is not None else None
        if folder is not None and folder.is_dir() and "00_目录.md" in message:
            os.startfile(folder)
            self.copy_text(message, "上传包已打开。先把里面的文件拖进 Copilot，再粘贴任务发送。")
            return
        self.copy_text(message, "任务已复制。到 Copilot 对话框粘贴并发送。")

    def poll_auto(self) -> None:
        try:
            if self.auto.get() and self.rt is not None and not self.busy:
                if time.monotonic() - self.last_active > 600:
                    self.auto.set(False)
                    self.say("10 分钟没有新指令，自动接收已关闭。", "warn")
                else:
                    seq = core.clipboard_sequence()
                    if seq not in (self.own_seq, self.auto_seen):
                        self.auto_seen = seq
                        text = core.clipboard_read_text()
                        if core.looks_like_directives(text, self.rt.session):
                            self.input.delete("1.0", "end")
                            self.input.insert("1.0", text)
                            if core.batch_needs_attention(text, self.rt):
                                self.say("自动接收只执行只读指令。这一批含有要确认、要修改或被拦截的内容，请在第 ③ 步点“执行”。", "warn")
                            else:
                                self.run_input("auto")
        finally:
            self.root.after(300, self.poll_auto)

    def check_idle(self) -> None:
        try:
            if self.rt is not None and not self.busy:
                if time.monotonic() - self.last_active > self.args.idle_exit * 60 and self.end_session("idle"):
                    self.say("空闲超过 %g 分钟，会话已自动结束。重新开始后，要把新的预设提示词贴给 Copilot。" % self.args.idle_exit, "warn")
        finally:
            self.root.after(30000, self.check_idle)

    def on_close(self) -> None:
        if self.busy:
            messagebox.showinfo(
                "正在执行", "正在执行指令，等它结束后再关闭（最长 %d 秒）。" % self.args.timeout, parent=self.root,
            )
            return
        if self.end_session("closed"):
            self.root.destroy()

    def on_error(self, exc_type, exc, tb) -> None:
        detail = "".join(traceback.format_exception(exc_type, exc, tb))
        messagebox.showerror("出错了", "%s：%s" % (exc_type.__name__, exc), detail=detail, parent=self.root)


def load_settings() -> dict:
    path = core.TOOL_DIR / "settings.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(values: dict) -> None:
    path = core.TOOL_DIR / "settings.json"
    current = load_settings()
    current.update(values)
    try:
        path.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def clipboard_files() -> list[str]:
    win = ctypes.wintypes
    core.shell32.DragQueryFileW.argtypes = [win.HANDLE, win.UINT, win.LPWSTR, win.UINT]
    core.shell32.DragQueryFileW.restype = win.UINT
    hwnd = core._create_owner()
    found: list[str] = []
    try:
        if not core._open_clipboard(hwnd):
            return []
        try:
            handle = core.user32.GetClipboardData(15)
            if not handle:
                return []
            count = core.shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
            for index in range(count):
                buf = ctypes.create_unicode_buffer(1024)
                core.shell32.DragQueryFileW(handle, index, buf, 1024)
                if buf.value:
                    found.append(buf.value)
        finally:
            core.user32.CloseClipboard()
    finally:
        core.user32.DestroyWindow(hwnd)
    return found


def set_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    set_dpi_awareness()
    root = tk.Tk()
    root.withdraw()
    try:
        base = core.build_parser().parse_args(argv)
    except SystemExit:
        messagebox.showerror("Copilot 执行器", "启动参数有误。可用参数见 python clip_agent.py --help。")
        return 4
    problem = core.validate_args(base)
    if problem:
        messagebox.showerror("Copilot 执行器", problem)
        return 4
    if core.is_admin():
        messagebox.showerror("Copilot 执行器", "拒绝以管理员身份运行。请用普通账户启动。")
        return 2
    if not core.acquire_mutex():
        messagebox.showerror("Copilot 执行器", "已经有一个执行器在运行（窗口版或控制台版）。先关掉它再启动。")
        return 3
    root.deiconify()
    App(root, base)
    root.mainloop()
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception as exc:  # 窗口版没有控制台，启动失败只能弹窗说明
        ctypes.windll.user32.MessageBoxW(None, "%s：%s" % (exc.__class__.__name__, exc), "Copilot 执行器启动失败", 0x10)
        code = 1
    raise SystemExit(code)
