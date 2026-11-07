#!/usr/bin/env python3
"""UTF-8 友好的字符串提取器。

`strings` 默认只认 ASCII，会把中文/多字节 UTF-8 截断；ArkTS 的 .abc 里
中文日志、字段名恰恰是最有价值的证据。这里按“可打印 ASCII + 高位字节”
切分连续片段再整体解码，保留中文。

用法:
    extract_strings.py <file> [-o out.txt] [--with-offset]
"""
import argparse
import re
import sys

RUN = re.compile(rb"[\x20-\x7e\x80-\xff]{4,}")


def extract(path):
    data = open(path, "rb").read()
    for m in RUN.finditer(data):
        raw = m.group()
        try:
            s = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        yield m.start(), s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("-o", "--output")
    ap.add_argument("--with-offset", action="store_true")
    args = ap.parse_args()

    out = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout
    n = 0
    try:
        for off, s in extract(args.file):
            n += 1
            out.write(f"{off}\t{s}\n" if args.with_offset else s + "\n")
    finally:
        if args.output:
            out.close()
    print(f"[extract_strings] {n} strings -> {args.output or 'stdout'}", file=sys.stderr)


if __name__ == "__main__":
    main()
