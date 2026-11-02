#!/usr/bin/env python3
"""Turn 复刻方案.md (and 拆解报告.md) into one readable web page.

    plan_preview.py <参考片目录>     # writes 复刻方案预览.html beside the Markdown

The Markdown stays the source of truth: other skills read it. The page is a view of it with a
contents sidebar, the key facts as cards, the segment table as a timeline with the reference's own
keyframes (``frames/<镜号>a.jpg``), and long technical blocks folded away. It is one self-contained
file that opens from disk in any browser; run the script again after the plan changes. The last
stdout line is one JSON object.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

MINIMUM_PYTHON = (3, 9)

PAGE = "复刻方案预览.html"
SOURCES = (("plan", "复刻方案", "复刻方案.md"), ("report", "拆解报告", "拆解报告.md"))
SHOT = re.compile(r"S\d{2,3}")


def emit(payload: Dict[str, object], code: int = 0) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    if code:
        sys.exit(code)


# ---------- inline Markdown ----------

def inline(text: str) -> str:
    codes: List[str] = []

    def keep(match: "re.Match[str]") -> str:
        codes.append(f"<code>{html.escape(match.group(1))}</code>")
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", keep, text)
    text = html.escape(text, quote=False)

    def image(m: "re.Match[str]") -> str:
        src = html.escape(html.unescape(m.group(2)), quote=True)
        return f'<img src="{src}" alt="{m.group(1)}" loading="lazy">'

    def link(m: "re.Match[str]") -> str:
        href = html.unescape(m.group(2))
        outward = href.startswith(("http://", "https://"))
        target = ' target="_blank" rel="noopener"' if outward else ""
        return f'<a href="{html.escape(href, quote=True)}"{target}>{m.group(1)}</a>'

    text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", image, text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)


def join_lines(lines: List[str]) -> str:
    out = ""
    for line in (s.strip() for s in lines):
        if out and not (re.search(r"[　-鿿＀-￯]$", out) and re.match(r"[　-鿿＀-￯]", line)):
            out += " "
        out += line
    return out


# ---------- block Markdown ----------

def cells(row: str) -> List[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def parse(md: str) -> List[Tuple[str, object]]:
    """Blocks: (kind, data). Kinds: h1..h4, p, quote, ul, ol, table, code, hr."""
    lines = md.replace("\r\n", "\n").split("\n")
    blocks: List[Tuple[str, object]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith("```"):
            lang = line[3:].strip()
            body: List[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            blocks.append(("code", (lang, "\n".join(body))))
            i += 1
            continue
        heading = re.match(r"(#{1,4})\s+(.*)", line)
        if heading:
            blocks.append((f"h{len(heading.group(1))}", heading.group(2).strip()))
            i += 1
            continue
        if re.match(r"\s*(-{3,}|\*{3,})\s*$", line):
            blocks.append(("hr", None))
            i += 1
            continue
        if line.lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"\s*\|?\s*:?-{2,}", lines[i + 1]):
            head = cells(line)
            rows = []
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(cells(lines[i]))
                i += 1
            blocks.append(("table", (head, rows)))
            continue
        if line.startswith(">"):
            body = []
            while i < len(lines) and lines[i].startswith(">"):
                body.append(lines[i][1:])
                i += 1
            blocks.append(("quote", join_lines(body)))
            continue
        item = re.match(r"(\s*)([-*]|\d+[.、])\s+(.*)", line)
        if item:
            kind = "ol" if item.group(2)[0].isdigit() else "ul"
            items: List[List[object]] = []
            while i < len(lines):
                m = re.match(r"(\s*)([-*]|\d+[.、])\s+(.*)", lines[i])
                if m:
                    items.append([len(m.group(1)), m.group(3)])
                elif lines[i].strip() and items and lines[i].startswith(" "):
                    items[-1][1] = join_lines([str(items[-1][1]), lines[i]])
                else:
                    break
                i += 1
            blocks.append((kind, items))
            continue
        body = []
        while i < len(lines) and lines[i].strip() and not re.match(r"(#{1,4}\s|```|>|\s*([-*]|\d+[.、])\s|\s*\|)", lines[i]):
            body.append(lines[i])
            i += 1
        if not body:
            body, i = [line], i + 1
        blocks.append(("p", join_lines(body)))
    return blocks


# ---------- special views ----------

def seconds(text: str) -> Optional[float]:
    m = re.search(r"(\d+)\s*分\s*(\d+(?:\.\d+)?)?\s*秒?", text)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2) or 0)
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else None


def clock(total: float) -> str:
    return f"{int(total // 60)}:{int(round(total % 60)):02d}"


def column(head: List[str], *words: str) -> Optional[int]:
    for n, h in enumerate(head):
        if any(w in h for w in words):
            return n
    return None


def segment_view(head: List[str], rows: List[List[str]], folder: Path) -> Tuple[str, float]:
    src, role = column(head, "原片"), column(head, "职责")
    look, say = column(head, "画面"), column(head, "台词", "内容", "文字")
    dur = column(head, "时长")
    lengths = [seconds(r[dur]) if dur is not None and dur < len(r) else None for r in rows]
    total = sum(x for x in lengths if x) or 0.0
    bar, cards = [], []
    for n, row in enumerate(rows):
        get = lambda k: row[k] if k is not None and k < len(row) else ""  # noqa: E731
        span = lengths[n] or (total / len(rows) if total else 1)
        bar.append(f'<a class="seg" href="#seg{n}" style="flex:{span:.2f}" title="{html.escape(row[0])}">'
                   f"{n + 1:02d}</a>")
        ids = SHOT.findall(get(src))
        thumbs = "".join(
            f'<img src="frames/{s}a.jpg" alt="原片 {s}" loading="lazy">'
            for s in dict.fromkeys(ids[:1] + ids[-1:]) if (folder / "frames" / f"{s}a.jpg").is_file()
        )
        parts = [
            f'<div class="seg-head"><span class="seg-no">{n + 1:02d}</span><h3>{inline(row[0])}</h3>'
            + (f'<span class="seg-time">{lengths[n]:.1f} 秒</span>' if lengths[n] else "") + "</div>",
            f'<p class="chip">{inline(get(role))}</p>' if get(role) else "",
            f'<p class="say">{inline(get(say))}</p>' if get(say) else "",
            f'<div class="look"><b>画面怎么动</b><p>{inline(get(look))}</p></div>' if get(look) else "",
            f'<div class="ref">{thumbs}<span>原片 {inline(get(src))}</span></div>' if get(src) else "",
        ]
        cards.append(f'<article class="seg-card" id="seg{n}">{"".join(parts)}</article>')
    note = f'<p class="muted">共 {len(rows)} 段，合计 {clock(total)}。点色块跳到那一段。</p>' if total else ""
    return f'<div class="timeline">{"".join(bar)}</div>{note}{"".join(cards)}', total


def facts_view(items: List[List[object]]) -> Optional[str]:
    pairs = [re.match(r"([^：:]{1,10})[：:]\s*(.+)", str(t)) for _, t in items]
    if len(items) < 2 or sum(1 for p in pairs if p) < 0.7 * len(items):
        return None
    out = []
    for p, (_, text) in zip(pairs, items):
        if p:
            out.append(f'<div class="fact"><span>{inline(p.group(1))}</span><p>{inline(p.group(2))}</p></div>')
        else:
            out.append(f'<div class="fact"><p>{inline(str(text))}</p></div>')
    return f'<div class="facts">{"".join(out)}</div>'


def list_html(kind: str, items: List[List[object]]) -> str:
    out, stack = "", []
    for indent, text in items:
        level = int(indent) // 2
        while len(stack) < level + 1:
            out += f"<{kind}>"
            stack.append(kind)
        while len(stack) > level + 1:
            out += f"</{stack.pop()}>"
        out += f"<li>{inline(str(text))}</li>"
    while stack:
        out += f"</{stack.pop()}>"
    return out


# ---------- page ----------

def render_doc(key: str, label: str, blocks: List[Tuple[str, object]], folder: Path) -> Tuple[str, str, Dict[str, str]]:
    body, toc, info = [], [], {}
    current = ""
    count = 0
    for kind, data in blocks:
        if kind == "h1":
            title = str(data)
            kicker, _, rest = title.partition("：")
            info["kicker"], info["title"] = (kicker, rest) if rest else (label, title)
            continue
        if kind == "h2":
            count += 1
            current = str(data)
            anchor = f"{key}-{count}"
            toc.append(f'<a href="#{anchor}"><i>{count:02d}</i>{html.escape(current)}</a>')
            body.append(f'<h2 id="{anchor}"><i>{count:02d} /</i> {inline(current)}</h2>')
        elif kind in ("h3", "h4"):
            body.append(f"<{kind}>{inline(str(data))}</{kind}>")
        elif kind == "p":
            text = str(data)
            if text.startswith("状态：") and "status" not in info:
                info["status"] = text[3:]
                continue
            body.append(f"<p>{inline(text)}</p>")
        elif kind == "quote":
            body.append(f"<blockquote>{inline(str(data))}</blockquote>")
        elif kind in ("ul", "ol"):
            body.append(facts_view(data) or list_html(kind, data))  # type: ignore[arg-type]
            for _, text in data:  # type: ignore[union-attr]
                m = re.match(r"(尺度|形式|画幅|新题材|题材)[：:]\s*(.+)", str(text))
                if m:
                    info.setdefault(m.group(1), m.group(2))
        elif kind == "table":
            head, rows = data  # type: ignore[misc]
            if "对照" in current and rows:
                view, total = segment_view(head, rows, folder)
                body.append(view)
                if total:
                    info.setdefault("total", clock(total))
                    info.setdefault("segments", str(len(rows)))
            else:
                th = "".join(f"<th>{inline(h)}</th>" for h in head)
                tr = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows)
                body.append(f'<div class="table"><table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>')
        elif kind == "code":
            lang, code = data  # type: ignore[misc]
            body.append(f'<details><summary>展开查看参数{(" · " + html.escape(lang)) if lang else ""}</summary>'
                        f"<pre><code>{html.escape(code)}</code></pre></details>")
        elif kind == "hr":
            body.append("<hr>")
    return "".join(body), "".join(toc), info


def build(folder: Path) -> Path:
    docs = []
    for key, label, name in SOURCES:
        path = folder / name
        if path.is_file():
            docs.append((key, label, render_doc(key, label, parse(path.read_text(encoding="utf-8")), folder)))
    if not docs:
        raise FileNotFoundError(f"no 复刻方案.md in {folder}")
    info = docs[0][2][2]
    def short(value: Optional[str]) -> Optional[str]:
        return re.split(r"[。；;，,、（(]", value)[0].strip()[:16] if value else None

    stats = [(info.get("total"), "建议片长"), (info.get("segments") and info["segments"] + " 段", "段落"),
             (short(info.get("尺度")), "尺度"), (short(info.get("形式") or info.get("画幅")), "形式")]
    stat_html = "".join(f'<div class="stat"><b>{inline(str(v))}</b><span>{k}</span></div>' for v, k in stats if v)
    toc = "".join(f'<p class="toc-group">{label}</p>{t}' for _, label, (_, t, _) in docs)
    parts = "".join(
        f'<section class="doc" id="{key}">' + ("" if n == 0 else f'<p class="doc-label">{label}</p>') + body + "</section>"
        for n, (key, label, (body, _, _)) in enumerate(docs)
    )
    title = info.get("title", "复刻方案")
    page = TEMPLATE.format(
        title=html.escape(title), kicker=html.escape(info.get("kicker", "复刻方案")),
        status=f'<p class="status">{inline(info["status"])}</p>' if info.get("status") else "",
        stats=f'<div class="stats">{stat_html}</div>' if stat_html else "", toc=toc, parts=parts,
        css=CSS, script=SCRIPT,
    )
    out = folder / PAGE
    out.write_bytes(page.encode("utf-8"))
    return out


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.version_info < MINIMUM_PYTHON:
        emit({"ok": False, "error": "needs Python %d.%d or newer" % MINIMUM_PYTHON}, 2)
    parser = argparse.ArgumentParser(description="Readable web preview of the remake plan.")
    parser.add_argument("folder", type=Path, help="the reference folder holding 复刻方案.md")
    args = parser.parse_args()
    try:
        page = build(args.folder.resolve())
    except (OSError, ValueError) as error:
        emit({"ok": False, "error": str(error)}, 1)
    emit({"ok": True, "page": str(page), "link": page.as_posix()})


CSS = """
:root{--paper:#f5f3ec;--card:#fffdf8;--ink:#1d211c;--muted:#6d6f68;--line:#e2ded2;--accent:#55663a;--soft:#e9ecdc;--warm:#f4ead0;--fs:17px}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--paper);color:var(--ink);font:var(--fs)/1.8 "PingFang SC","Microsoft YaHei","Noto Sans SC",system-ui,sans-serif}
aside{position:fixed;top:0;left:0;bottom:0;width:240px;padding:28px 18px;border-right:1px solid var(--line);overflow-y:auto;background:var(--paper)}
aside .brand{font-weight:700;font-size:16px;margin:0 0 20px}
aside a{display:flex;gap:10px;padding:7px 10px;border-radius:6px;color:var(--ink);text-decoration:none;font-size:14px;line-height:1.5}
aside a:hover{background:var(--soft)}aside a i{color:var(--muted);font-style:normal;font-family:ui-monospace,Consolas,monospace;font-size:12px;padding-top:2px}
.toc-group{margin:18px 0 6px;font-size:12px;color:var(--muted);letter-spacing:.08em}
.tools{display:flex;gap:8px;margin-top:20px}.tools button{flex:1;border:1px solid var(--line);background:var(--card);border-radius:6px;padding:6px;font:inherit;font-size:13px;cursor:pointer}
main{margin-left:240px;padding:40px 56px 120px;max-width:1060px}
.kicker{display:inline-block;background:var(--warm);border-radius:4px;padding:3px 10px;font-size:13px;color:#6b5a1f}
h1{font-size:42px;line-height:1.25;margin:16px 0 12px;letter-spacing:-.01em}
.status{color:var(--muted);margin:0 0 24px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));border-top:1px solid var(--line);border-bottom:1px solid var(--line);margin:8px 0 32px}
.stat{padding:16px 18px;border-left:1px solid var(--line)}.stat:first-child{border-left:0;padding-left:0}
.stat b{display:block;font-size:22px;line-height:1.3}.stat span{font-size:13px;color:var(--muted)}
h2{font-size:26px;margin:56px 0 16px;padding-top:12px}h2 i{color:var(--muted);font-style:normal;font-size:14px;font-family:ui-monospace,Consolas,monospace;margin-right:6px}
h3{font-size:19px;margin:24px 0 8px}h4{font-size:17px;margin:20px 0 6px}
p{margin:10px 0}a{color:var(--accent)}code{background:var(--soft);border-radius:4px;padding:1px 6px;font-size:.9em}
blockquote{margin:20px 0;padding:18px 22px;border-left:4px solid var(--accent);background:var(--card);font-size:1.15em;font-weight:600;border-radius:0 8px 8px 0}
ul,ol{padding-left:1.4em}li{margin:6px 0}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin:16px 0}
.fact{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}.fact span{font-size:13px;color:var(--accent);font-weight:700}.fact p{margin:4px 0 0}
.table{overflow-x:auto;margin:16px 0;border:1px solid var(--line);border-radius:10px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:15px;line-height:1.65}th,td{padding:10px 14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{background:var(--soft);white-space:nowrap;position:sticky;top:0}tr:last-child td{border-bottom:0}
details{margin:14px 0;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 16px}summary{cursor:pointer;color:var(--muted);font-size:14px}
pre{overflow-x:auto;font-size:13px;line-height:1.6;margin:10px 0 4px}
.timeline{display:flex;gap:3px;margin:18px 0 6px;height:34px}.seg{display:flex;align-items:center;justify-content:center;min-width:26px;background:var(--soft);color:var(--ink);text-decoration:none;font-size:12px;font-family:ui-monospace,Consolas,monospace;border-radius:4px}
.seg:hover{background:var(--accent);color:#fff}.muted{color:var(--muted);font-size:13px}
.seg-card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px 24px;margin:16px 0;scroll-margin-top:16px}
.seg-head{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}.seg-head h3{margin:0;flex:1}
.seg-no{font:700 28px/1 Georgia,serif;color:var(--accent)}.seg-time{font-size:13px;color:var(--muted);font-family:ui-monospace,Consolas,monospace}
.chip{display:inline-block;margin:10px 0 2px;padding:2px 10px;border:1px solid var(--line);border-radius:999px;font-size:13px;color:var(--muted)}
.say{font-size:1.05em}.look{background:var(--soft);border-radius:8px;padding:10px 14px;margin:10px 0}.look b{font-size:13px;color:var(--accent)}.look p{margin:4px 0 0}
.ref{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:12px;font-size:13px;color:var(--muted)}.ref img{height:72px;border-radius:6px;border:1px solid var(--line)}
img{max-width:100%;border-radius:8px}hr{border:0;border-top:1px solid var(--line);margin:32px 0}
.doc-label{margin:80px 0 0;padding-top:24px;border-top:2px solid var(--ink);font-weight:700;letter-spacing:.1em}
footer{margin-top:64px;color:var(--muted);font-size:13px}
body.big{--fs:20px}
@media (max-width:900px){aside{position:static;width:auto;border-right:0;border-bottom:1px solid var(--line)}main{margin-left:0;padding:24px 16px 80px}h1{font-size:30px}}
@media print{aside{display:none}main{margin:0;padding:0}details{display:block}details>*{display:block}.seg-card{break-inside:avoid}}
"""

SCRIPT = """
document.getElementById('big').onclick=()=>document.body.classList.toggle('big');
document.getElementById('print').onclick=()=>{document.querySelectorAll('details').forEach(d=>d.open=true);window.print();};
"""

TEMPLATE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{css}</style></head>
<body>
<aside><p class="brand">复刻方案预览</p>{toc}<div class="tools"><button id="big">大字</button><button id="print">打印</button></div></aside>
<main>
<span class="kicker">{kicker}</span>
<h1>{title}</h1>
{status}{stats}
{parts}
<footer>本页由复刻方案.md 自动生成。方案以 Markdown 文件为准；改了文件，刷新本页就是新的。</footer>
</main>
<script>{script}</script>
</body></html>
"""

if __name__ == "__main__":
    main()
