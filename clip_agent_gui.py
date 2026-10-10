"""Copilot 执行器的窗口版。

不监听剪贴板：只有你在输入框里按 Ctrl+V（或右键“粘贴并执行”）时，输入框才读取一次剪贴板。
解析、策略、执行、脱敏和审计都走 clip_agent 里同一套代码。
"""

from __future__ import annotations

import copy
import ctypes
import queue
import threading
import time
import tkinter as tk
import traceback
import winsound
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import clip_agent as core

FRONT_END = Path(__file__).resolve()
UI_FONT = ("Microsoft YaHei UI", 10)
BOLD_FONT = ("Microsoft YaHei UI", 10, "bold")
TITLE_FONT = ("Microsoft YaHei UI", 15, "bold")
CODE_FONT = ("Consolas", 10)
MODE_NAMES = {"readonly": "只读严格", "tiered": "分级确认"}
TONES = {"ok": "#1d6b2f", "info": "#333333", "warn": "#9a5b00", "error": "#b3261e"}
DECISION_NAMES = {"allow": "执行", "block": "拦截", "marker": "拦截", "deny": "拒绝", "skip": "跳过"}


def task_templates(mode: str) -> dict[str, str]:
    if mode == "tiered":
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
        root.after(30000, self.check_idle)

    def build_setup(self) -> None:
        frame = self.setup = ttk.Frame(self.root, padding=24)
        ttk.Label(frame, text="Copilot 执行器", font=TITLE_FONT).grid(row=0, column=0, columnspan=3, sticky="w")
        auto_wrap(ttk.Label(
            frame,
            text="把 Copilot 回复里的指令粘贴进来执行，结果自动复制回剪贴板，你再贴回 Copilot。"
            "不联网，不操作浏览器，不监听剪贴板。",
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
        ttk.Button(frame, text="开始会话", command=self.start_session).grid(row=4, column=1, sticky="w", pady=(24, 0))
        self.setup_status = auto_wrap(ttk.Label(frame, textvariable=self.status))
        self.setup_status.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(18, 0))
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
        task_frame = ttk.Frame(step2)
        task_frame.pack(fill="x", pady=(6, 0))
        self.task = scrolled_text(task_frame, height=3, wrap="word", font=UI_FONT, undo=True)

        self.step3 = ttk.LabelFrame(frame, padding=8)
        self.step3.pack(fill="both", expand=True, pady=(10, 0))
        self.input = scrolled_text(self.step3, height=8, wrap="none", font=CODE_FONT, undo=True)
        self.input.bind("<<Paste>>", self.on_paste)
        self.input.bind("<Control-Return>", lambda _event: (self.run_input(), "break")[1])
        self.input.bind("<Button-3>", self.input_menu)
        buttons = ttk.Frame(self.step3)
        buttons.pack(fill="x", pady=(6, 0))
        self.run_button = ttk.Button(buttons, text="执行（Ctrl+Enter）", command=self.run_input)
        self.run_button.pack(side="left")
        ttk.Button(buttons, text="清空", command=self.clear_input).pack(side="left", padx=8)
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="粘贴并执行", command=lambda: self.input.event_generate("<<Paste>>"))
        self.menu.add_command(label="清空", command=self.clear_input)

        self.step4 = ttk.LabelFrame(frame, padding=8)
        self.step4.pack(fill="both", expand=True, pady=(10, 0))
        self.result = scrolled_text(self.step4, height=10, wrap="word", font=CODE_FONT, state="disabled")
        ttk.Button(self.step4, text="再次复制结果", command=self.copy_result).pack(anchor="w", pady=(6, 0))

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
        mode_short = "只读" if args.mode == "readonly" else "分级"
        self.root.title("Copilot 执行器 [%s] %s" % (mode_short, rt.session))
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
        self.say("会话已开始，预设提示词已复制到剪贴板。到 Copilot 对话框粘贴并发送。", "ok")

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

    def copy_task(self) -> None:
        text = self.task.get("1.0", "end-1c").strip()
        if not text:
            self.say("任务是空的。先选一个模板或自己写一句。", "warn")
            return
        self.copy_text(text, "任务已复制。到 Copilot 对话框粘贴并发送，把它回复里的代码块复制到第 ③ 步。")

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

    def run_input(self) -> None:
        if self.busy or self.rt is None:
            return
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
                holder["ok"] = ConfirmDialog(self.root, command, reason).ok
            finally:
                done.set()
        elif kind == "reply":
            self.set_busy(False)
            self.show_reply(message[1])
        elif kind == "error":
            self.set_busy(False)
            self.say("执行出错：%s" % message[1], "error")

    def show_reply(self, reply: core.Reply) -> None:
        self.last_active = time.monotonic()
        if reply.payload is None:
            self.say("\n".join(reply.notes), "warn")
            return
        self.last_payload = reply.payload
        set_text(self.result, reply.payload)
        try:
            seq = core.clipboard_write(reply.payload)
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
