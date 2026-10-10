# 候选方案评估

目标是：人在 M365 Copilot 网页里复制一条固定格式的指令，本地程序执行只读命令或读文件，再把结果交还给人粘贴回去。不能调用其他模型，不能对 Copilot 页面做自动化。

结论是不改这四个项目，单独写一个只用 Python 标准库的小程序。下面按同一格式记录原因。

## agent-cli（basnijholt/agent-cli）

**4/10。** 能改出剪贴板读写，但每个命令都要调用模型，Windows 支持作者自己也标明没在真机上验证过，不值得接进来。

原理：这是一个命令行工具集。文本类命令注册在 `agent_cli/agents/`，用 `@app.command(...)` 挂到命令行。剪贴板通过 `get_clipboard_text` 和 `pyperclip.copy` 进出。`agent_cli/_tools.py` 里的 `read_file` 是 `Path(path).read_text()`，没有指定编码；`execute_code` 是 `subprocess.run(code.split(), capture_output=True, text=True, check=True)`，不经过 shell，也没有超时。命令入口本身还标记了需要模型。

如果硬要改，动这几处，而不是在现有命令上打补丁：

- 新增 `agent_cli/agents/clip_tool.py`，用正则识别 `TOOL[会话码]:` 和 `READ_FILE[会话码]:`，不要复用会去调模型的命令。
- 执行改成带超时的 PowerShell，读文件单独做编码探测和目录限制。`execute_code` 的 `split()` 不能保留引号和管道。
- 结果用 `pyperclip.copy` 写回。Windows 文档里的 AutoHotkey 示例是 `#+a::{ Run("agent-cli autocorrect", , "Hide") }` 这种热键，隐藏运行，不适合作为企业里的默认形态。

优点是项目还在维护，许可证是 MIT，剪贴板读写已经有现成函数。缺点是模型依赖去不掉、Windows 路径没有实测、执行器没有超时和策略。企业风险在于隐藏窗口加热键，和“人明确把指令交给程序”不是一回事。

## AgentShot（JunchenMeteor/AgentShot）

**3/10。** 它监听的是剪贴板里的图片，不是文本指令；安装方式本身就是企业终端检测会标红的模式。

原理：`bin/agentshot.js` 拉起 `powershell.exe -NoProfile -ExecutionPolicy Bypass -Command`，用 `Add-Type` 调用 `GetClipboardSequenceNumber`，大约每 1.5 秒看一次剪贴板。安装用 `schtasks /SC ONLOGON` 做登录自启，安装脚本是 `irm | iex`。

没有可复用的“识别 TOOL / READ_FILE 并执行”的代码。要改就等于重写监听对象、去掉 Bypass、去掉隐藏和自启，最后剩下的只有“轮询序列号”这一小段，而这一段用标准库的 ctypes 就能写。

优点是序列号轮询不需要一直打开剪贴板。缺点是星标和提交都很少，只处理图片，而且 Bypass、隐藏窗口、登录自启和 `irm | iex` 叠在一起。企业风险高，不适合作为起点。

## AI-Shell（AgentMatthy/AI-Shell）

**2/10。** 只支持 Linux，还要调用外部模型接口，许可证是 WTFPL，不能拿来跑。

原理：用户在 shell 里给出自然语言，程序调用 OpenRouter 一类的接口生成命令。`command_safety.py` 采用失败即拒绝的检查，这个方向可以借鉴：匹配不上安全规则就拒绝，而不是匹配上危险规则才拒绝。

没有 Windows 剪贴板，也没有“人不把命令交给模型、只把命令交给本地执行器”的路径。要改等于换掉操作系统假设和模型调用，没有可保留的主体。

优点是安全检查默认关闭未知命令。缺点是平台、许可证和外部接口都不符合约束。企业风险是命令会离开本机去调用第三方模型。

## LangGraph（langchain-ai/langgraph）

**作为运行时依赖是 1/10，作为循环设计的参考是 8/10。** 它需要在代码里调用模型，而这里没有能从程序调用的 Copilot 接口。

原理：图里的节点执行到 `interrupt()` 就暂停，外面的人批准或拒绝后，用 `Command(resume=...)` 恢复。检查点对应一次运行的步号。节点从中断处恢复时，中断前的代码会再执行一遍，所以同一次输入要去重。

对应到这个程序：

- `interrupt()` 近似于把结果写回剪贴板，然后等下一次粘贴。
- `Command(resume=...)` 近似于用户粘贴进来的下一批指令。
- 检查点和线程号近似于步号加审计记录。
- 批准或拒绝近似于分级档里的 `y`。

不引入这个库。网页聊天没有可调用的模型接口，加一层图运行时只增加依赖，不增加能力。

## 明确不采用的做法

用 Playwright、Puppeteer 或浏览器调试协议去读、去填 M365 Copilot 页面，等同于劫持会话。多数网页聊天的使用条款也不允许自动化“从页面复制、再粘贴回页面”这两步。模拟按键还有贴错窗口的风险，例如贴进对外的聊天。这条路线不实现，也不作为后备。

可以参考、但同样不接入的边界：Aider 的复制粘贴模式把“从网页复制”和“粘贴回网页”留给人手；本程序再进一步，连“把指令交给执行器”也由人粘贴完成。剪贴板写入用 Win32，序列号在 `CloseClipboard` 之后读取，避免把自己刚写回去的内容又当成新指令。

## 实际采用

`clip_agent.py` 只用 Python 标准库，策略、执行、脱敏、审计都在这一层。日常入口是同目录的窗口版 `clip_agent_gui.py`，控制台仍可用。窗口不监听剪贴板，只在人把 Copilot 的代码块粘进输入框时执行。如果以后公司批准了 Microsoft 365 Copilot 的官方接口或 Copilot Studio 的工具接入，可以只换输入这一层，白名单和审计不动。官方接口需要许可证、应用注册和管理员同意，而且预览接口不适合直接当生产通道，那是另一个项目。
