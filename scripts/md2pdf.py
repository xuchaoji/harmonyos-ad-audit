#!/usr/bin/env python3
"""Markdown -> 带封面/目录/页码的中文 PDF（pandoc + weasyprint）。

用法:
  md2pdf.py report.md report.pdf \
      --title "某某 App 广告审计" --subtitle "HarmonyOS HAP 静态分析" \
      --meta "包名=com.example" --meta "版本=1.0" --date 2026-11-06

依赖: pandoc, weasyprint, 中文字体（WenQuanYi / Noto Sans CJK 任一）
"""
import argparse
import html
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CSS = r"""
@page {
  size: A4;
  margin: 17mm 15mm 17mm 15mm;
  @top-left { content: "__HEADER__"; font-size: 7.8pt; color: #8a8f98; }
  @top-right { content: "__DATE__"; font-size: 7.8pt; color: #8a8f98; }
  @bottom-center { content: counter(page) " / " counter(pages); font-size: 7.8pt; color: #8a8f98; }
}
@page cover {
  margin: 0;
  background: #0d1b2a;
  @top-left { content: none }
  @top-right { content: none }
  @bottom-center { content: none }
}
html, body { max-width: none !important; margin: 0 !important; padding: 0 !important; background: transparent !important; }
html { font-family: "__FONT__", sans-serif; }
body { font-size: 9.9pt; line-height: 1.62; color: #1b1d21; }
section.cover { page: cover; width: 210mm; height: 297mm; box-sizing: border-box;
  padding: 34mm 22mm 20mm; page-break-after: always; color: #fff; position: relative; }
.cover .kicker { font-size: 8.5pt; letter-spacing: 2.4pt; color: #7fd1c7;
  border-left: 3px solid #7fd1c7; padding-left: 8pt; }
.cover-title { color: #fff; font-size: 30pt; line-height: 1.28; margin: 14mm 0 0; font-weight: 700; }
.cover-sub { margin-top: 6mm; font-size: 11pt; color: #9fb6cc; }
.cover-meta { position: absolute; left: 22mm; right: 22mm; bottom: 42mm; font-size: 9pt; }
.cover-meta .row { display: flex; padding: 4.6pt 0; }
.cover-meta .k { width: 20mm; flex: none; color: #7fd1c7; white-space: nowrap; }
.cover-meta .v { flex: 1; color: #dbe6f2; }
.cover-foot { position: absolute; left: 22mm; right: 22mm; bottom: 18mm; font-size: 8pt; color: #8aa0b5; }
h1, h2, h3, h4 { font-weight: 700; color: #10243a; page-break-after: avoid; }
h1 { font-size: 18pt; margin: 0 0 8pt; }
h2 { font-size: 15.5pt; margin: 0 0 7pt; padding-bottom: 4pt; border-bottom: 1.2pt solid #e3e8ef; page-break-before: always; }
h2:first-of-type { page-break-before: avoid; }
h3 { font-size: 11.6pt; margin: 13pt 0 5pt; color: #17395c; }
h4 { font-size: 10.2pt; margin: 10pt 0 4pt; }
p { margin: 5pt 0; }
ul, ol { margin: 5pt 0 5pt 14pt; padding: 0; }
li { margin: 2.2pt 0; }
strong { color: #0b2b4a; }
code { font-family: "DejaVu Sans Mono", monospace; font-size: 8.2pt; color: #b3331d; }
pre { background: #f6f8fa; border: 0.5pt solid #e1e6ec; border-left: 2.4pt solid #7aa7c7;
  padding: 6pt 8pt; margin: 6pt 0; white-space: pre-wrap; word-break: break-all; page-break-inside: avoid; }
pre code { color: #1f2933; font-size: 7.9pt; }
table { display: table !important; width: 100% !important; border-collapse: collapse; margin: 6pt 0 8pt; font-size: 8.3pt; border: none; }
thead { background: #eef3f8; }
th { text-align: left; padding: 3.6pt 4pt; border-bottom: 0.8pt solid #b9c7d6; color: #10243a; }
td { padding: 3.4pt 4pt; border-bottom: 0.4pt solid #dfe6ee; vertical-align: top; overflow-wrap: anywhere; }
tr { page-break-inside: avoid; }
blockquote { margin: 6pt 0; padding: 5pt 9pt; background: #fff9ec; border-left: 2.4pt solid #e0b252; color: #5a4a24; font-size: 8.9pt; }
blockquote p { margin: 2pt 0; }
a { color: #0b5cad; text-decoration: none; }
hr { border: none; border-top: 0.6pt solid #dde4ec; margin: 10pt 0; }
nav#TOC { page-break-after: always; font-size: 9.2pt; }
nav#TOC ul { list-style: none; margin: 0; padding: 0; }
nav#TOC ul ul { margin-left: 12pt; }
nav#TOC li { margin: 2.6pt 0; }
nav#TOC a { color: #1b1d21; }
nav#TOC a::after { content: target-counter(attr(href), page); float: right; color: #8a8f98; }
nav#TOC::before { content: "目 录"; display: block; font-size: 15.5pt; font-weight: 700;
  color: #10243a; border-bottom: 1.2pt solid #e3e8ef; padding-bottom: 4pt; margin-bottom: 8pt; }
"""


def detect_font():
    try:
        out = subprocess.run(["fc-match", "-f", "%{family[0]}", "sans-serif:lang=zh"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        if out:
            return out
    except Exception:
        pass
    return "WenQuanYi Micro Hei"


def split_cover(md):
    """去掉文首标题块（H1 + 元信息 + 引用 + 分隔线），正文从第一个 ## 开始。"""
    lines = md.splitlines()
    first_h1 = next((i for i, l in enumerate(lines[:20]) if l.startswith("# ")), None)
    if first_h1 is None:
        return md
    first_h2 = next((i for i, l in enumerate(lines) if re.match(r"^##\s", l)), None)
    if first_h2 is None:
        return "\n".join(lines[first_h1 + 1:])
    return "\n".join(lines[first_h2:])


def build_toc(fragment):
    heads = re.findall(r'<h([23]) id="([^"]+)"[^>]*>(.*?)</h\1>', fragment, re.S)
    if not heads:
        return ""
    items = []
    for level, hid, text in heads:
        text = re.sub(r"<[^>]+>", "", text).strip()
        items.append(f'<li class="l{level}"><a href="#{hid}">{html.escape(text)}</a></li>')
    return "<nav id=\"TOC\"><ul>" + "".join(items) + "</ul></nav>"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("md")
    ap.add_argument("pdf")
    ap.add_argument("--title")
    ap.add_argument("--subtitle", default="HarmonyOS HAP 静态分析")
    ap.add_argument("--meta", action="append", default=[], help="k=v，可重复")
    ap.add_argument("--date", default=None)
    ap.add_argument("--header", default=None, help="页眉文字，默认用标题")
    ap.add_argument("--foot", default="本报告仅用于安全研究与广告效果核验，请勿用于未授权测试。")
    ap.add_argument("--keep-html", action="store_true")
    args = ap.parse_args()

    for bin_ in ("pandoc", "weasyprint"):
        if not shutil.which(bin_):
            sys.exit(f"缺少 {bin_}，请先安装")

    md_path = Path(args.md)
    md = md_path.read_text(encoding="utf-8")
    body = split_cover(md)

    title = args.title
    if not title:
        m = re.search(r"^#\s+(.+)$", md, re.M)
        title = m.group(1).strip() if m else md_path.stem
    date = args.date or __import__("datetime").date.today().isoformat()

    with tempfile.TemporaryDirectory() as td:
        bmd = Path(td) / "body.md"
        bmd.write_text(body, encoding="utf-8")
        frag = subprocess.run(
            ["pandoc", "-f", "gfm", "-t", "html5", "--no-highlight", str(bmd)],
            capture_output=True, text=True, check=True).stdout

    toc = build_toc(frag)

    meta_rows = ""
    for kv in args.meta:
        if "=" in kv:
            k, v = kv.split("=", 1)
            meta_rows += (f'<div class="row"><span class="k">{html.escape(k)}</span>'
                          f'<span class="v">{html.escape(v)}</span></div>')

    css = (CSS.replace("__HEADER__", html.escape(args.header or title, quote=True))
              .replace("__DATE__", date)
              .replace("__FONT__", detect_font()))

    page = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>{css}</style></head><body>
<section class="cover">
  <div class="kicker">SECURITY &amp; AD-TECH AUDIT</div>
  <h1 class="cover-title">{html.escape(title)}</h1>
  <div class="cover-sub">{html.escape(args.subtitle)}</div>
  <div class="cover-meta">{meta_rows}</div>
  <div class="cover-foot">{html.escape(args.foot)}</div>
</section>
{toc}
{frag}
</body></html>"""

    out_html = Path(args.pdf).with_suffix(".html")
    out_html.write_text(page, encoding="utf-8")
    subprocess.run(["weasyprint", str(out_html), args.pdf], check=True)
    if not args.keep_html:
        out_html.unlink(missing_ok=True)
    print(f"[md2pdf] -> {args.pdf}")


if __name__ == "__main__":
    main()
