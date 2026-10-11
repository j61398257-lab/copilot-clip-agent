"""PROTOTYPE：实测 Edge 侧边栏 Copilot 一页实际能读多少字。用完即删，不并入主程序。

生成：python prototype_page_probe.py make
核对：python prototype_page_probe.py check 50k        （从剪贴板读 Copilot 的回答）
      python prototype_page_probe.py check 50k 回答.txt
"""

import html
import json
import random
import re
import sys
from pathlib import Path

SIZES = {"20k": 20_000, "50k": 50_000, "100k": 100_000, "200k": 200_000, "400k": 400_000}
MARKS = 40
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
OUT = Path(__file__).resolve().parent / "page_probe"
ANSWERS = OUT / "answers.json"

PROMPT = """只看当前标签页的内容。页面里分散着形如【校验点 NN/40：XXXXXX】的标记。
请按出现顺序逐行列出你在页面里实际看到的全部校验点，每行只写：NN XXXXXX
没看到的不要补写，也不要猜。
最后单独一行写：最后看到的校验点 NN"""

DIRS = ["app/api", "app/services", "app/integrations", "app/models", "app/core", "web/src/pages", "web/src/components", "tests", "scripts"]
NOUNS = ["Retrieval", "Document", "Chunk", "Vector", "Session", "Upload", "Index", "Rerank", "Embedding", "Policy", "Audit", "Cache"]
VERBS = ["search", "load", "build", "merge", "score", "parse", "flush", "refresh", "validate", "dispatch", "render", "sync"]
NOTES = ["负责参数校验和默认值", "调用下游服务并合并结果", "读取配置后初始化客户端", "失败时重试三次再抛出", "把结果按分数排序后截断", "写入缓存并设置过期时间", "记录审计日志", "转换为响应模型"]


def code(rng: random.Random) -> str:
    return "".join(rng.choice(ALPHABET) for _ in range(6))


def filler(rng: random.Random, n: int) -> str:
    d = rng.choice(DIRS)
    noun = rng.choice(NOUNS)
    verb = rng.choice(VERBS)
    start = rng.randint(1, 900)
    return "%s/%s_%03d.py  L%d-%d  %sService%d.%s(query, top_k)  %s；调用 %s.%s" % (
        d, noun.lower(), n, start, start + rng.randint(8, 120), noun, n % 50, verb,
        rng.choice(NOTES), rng.choice(NOUNS) + "Store", rng.choice(VERBS),
    )


def build(size: int, codes: list[str], rng: random.Random) -> list[str]:
    targets = [round(k * size / (MARKS + 1)) for k in range(1, MARKS + 1)]
    lines, total, n, k = [], 0, 0, 0
    while total < size:
        if k < MARKS and total >= targets[k]:
            line = "【校验点 %02d/%d：%s】" % (k + 1, MARKS, codes[k])
            k += 1
        else:
            n += 1
            line = filler(rng, n)
        lines.append(line)
        total += len(line) + 1
    while k < MARKS:
        lines.append("【校验点 %02d/%d：%s】" % (k + 1, MARKS, codes[k]))
        k += 1
    return lines


def write_html(path: Path, label: str, lines: list[str]) -> None:
    blocks, chunk = [], []
    for i, line in enumerate(lines, 1):
        chunk.append(html.escape(line))
        if i % 60 == 0:
            blocks.append(chunk)
            chunk = []
    if chunk:
        blocks.append(chunk)
    body = "\n".join(
        "<h2>第 %d 段</h2>\n<pre>%s</pre>" % (i, "\n".join(b)) for i, b in enumerate(blocks, 1)
    )
    path.write_text(
        "<!doctype html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<title>代码库地图读取实测 %s</title></head><body>\n<h1>代码库地图读取实测 %s</h1>\n%s\n</body></html>\n"
        % (label, label, body),
        encoding="utf-8",
    )


def make() -> None:
    OUT.mkdir(exist_ok=True)
    rng = random.Random()
    answers = {}
    for label, size in SIZES.items():
        codes = [code(rng) for _ in range(MARKS)]
        lines = build(size, codes, random.Random(size))
        chars = sum(len(x) + 1 for x in lines)
        write_html(OUT / ("probe_%s.html" % label), label, lines)
        (OUT / ("probe_%s.txt" % label)).write_text("\n".join(lines) + "\n", encoding="utf-8")
        answers[label] = {"chars": chars, "codes": codes}
        print("probe_%s：%d 字，%d 个校验点" % (label, chars, MARKS))
    ANSWERS.write_text(json.dumps(answers, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "提示词.txt").write_text(PROMPT + "\n", encoding="utf-8")
    print("\n已生成到：%s" % OUT)
    print("不要在 Edge 里打开 answers.json，侧边栏 Copilot 可能读到其他标签页。")
    print("\n贴给侧边栏 Copilot 的提示词（也存在 提示词.txt）：\n")
    print(PROMPT)


def read_reply(argv: list[str]) -> str:
    if argv:
        return Path(argv[0]).read_text(encoding="utf-8-sig")
    import tkinter

    root = tkinter.Tk()
    root.withdraw()
    try:
        return root.clipboard_get()
    finally:
        root.destroy()


def check(label: str, argv: list[str]) -> None:
    answers = json.loads(ANSWERS.read_text(encoding="utf-8"))
    label = label.lower()
    if label not in answers:
        sys.exit("没有 %s，可选：%s" % (label, "、".join(answers)))
    want = answers[label]["codes"]
    chars = answers[label]["chars"]
    reply = read_reply(argv).upper()
    got: dict[int, str] = {}
    for line in reply.splitlines():
        line = re.sub(r"[/／]\s*%d" % MARKS, " ", line)
        m = re.search(r"(?<!\d)(\d{1,2})(?!\d)\D{0,12}?([A-Z0-9]{6})(?![A-Z0-9])", line)
        if m and 1 <= int(m.group(1)) <= MARKS:
            got.setdefault(int(m.group(1)), m.group(2))
    marks, right, wrong = [], 0, 0
    for i, expect in enumerate(want, 1):
        value = got.get(i)
        if value == expect:
            marks.append("✓")
            right += 1
        elif value:
            marks.append("✗")
            wrong += 1
        else:
            marks.append("·")
    run = 0
    for m in marks:
        if m != "✓":
            break
        run += 1
    spacing = chars / (MARKS + 1)
    print("页面 %s：共 %d 字，每隔约 %d 字一个校验点" % (label, chars, spacing))
    print("逐点结果（✓ 对  ✗ 码不对  · 没列出）：")
    for start in range(0, MARKS, 10):
        print("  %02d-%02d  %s" % (start + 1, start + 10, " ".join(marks[start:start + 10])))
    print("答对 %d 个，码不对 %d 个，没列出 %d 个" % (right, wrong, MARKS - right - wrong))
    if run == MARKS:
        print("结论：整页都读到了（至少 %d 字）" % chars)
    else:
        print("结论：从开头连续读到第 %d 个校验点，约 %d 字；之后不可靠" % (run, run * spacing))
    if wrong:
        print("注意：有 %d 个码对不上，说明它会编造没读到的内容" % wrong)


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "make":
        make()
    elif len(sys.argv) >= 3 and sys.argv[1] == "check":
        check(sys.argv[2], sys.argv[3:])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
