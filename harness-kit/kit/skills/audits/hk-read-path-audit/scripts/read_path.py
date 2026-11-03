#!/usr/bin/env python3
"""Size a set of documents and find the passages they state more than once.

    read_path.py <file or directory>...        # a directory expands to its .md .csv .txt .html files
    read_path.py --min-words 8 ...             # shortest sentence that counts as a restatement

Prints one table (file, lines, words, approximate tokens) and then every sentence that
appears, after normalisation, more than once, across files or within one, with file:line for each place.
Normalisation ignores case, backticks, quotes, punctuation and spacing, so a rule copied
into a template with the path re-quoted still matches. Tokens are words times 1.35, close
enough to rank files by; the audit says "about".
"""
import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

EXT = {".md", ".csv", ".txt", ".html"}


def expand(paths):
    out = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out += sorted(q for q in p.rglob("*") if q.suffix in EXT and q.is_file())
        elif p.is_file():
            out.append(p)
        else:
            sys.exit(f"not found: {p}")
    seen, uniq = set(), []
    for p in out:
        r = p.resolve()
        if r not in seen:
            seen.add(r)
            uniq.append(p)
    return uniq


def normalise(s):
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[`'\"“”‘’*_#>|\[\]()]", " ", s.lower())
    s = re.sub(r"[^a-z0-9./-]+", " ", s)
    return " ".join(s.split())


def sentences(text):
    """Yield (line_number, sentence) over a file; a sentence ends at . ! ? or a line break that ends a list item."""
    for i, line in enumerate(text.splitlines(), 1):
        for part in re.split(r"(?<=[.!?])\s+", line.strip()):
            if part:
                yield i, part


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--min-words", type=int, default=8)
    a = ap.parse_args()
    files = expand(a.paths)
    if not files:
        sys.exit("no readable files")

    rows, total = [], 0
    where = defaultdict(list)
    for f in files:
        text = f.read_text(errors="replace")
        words = len(text.split())
        tokens = int(words * 1.35)
        total += tokens
        rows.append((str(f), text.count("\n") + 1, words, tokens))
        for ln, s in sentences(text):
            n = normalise(s)
            if len(n.split()) >= a.min_words:
                where[n].append((str(f), ln, s.strip()))

    w = max(len(r[0]) for r in rows)
    print(f"{'file':<{w}}  {'lines':>5}  {'words':>6}  {'~tokens':>7}")
    for r in rows:
        print(f"{r[0]:<{w}}  {r[1]:>5}  {r[2]:>6}  {r[3]:>7}")
    print(f"{'total':<{w}}  {'':>5}  {'':>6}  {total:>7}")

    dup = {k: v for k, v in where.items() if len(v) > 1}
    print(f"\nrestated passages: {len(dup)} (sentences of {a.min_words}+ words found more than once, across files or within one)")
    for k, v in sorted(dup.items(), key=lambda kv: -len(kv[1])):
        print(f"\n  \"{v[0][2][:140]}\"")
        for p, ln, _ in v:
            print(f"    {p}:{ln}")


if __name__ == "__main__":
    main()
