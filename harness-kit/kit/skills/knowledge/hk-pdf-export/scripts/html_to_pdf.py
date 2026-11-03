#!/usr/bin/env python3
"""Turn an HTML page into a house-styled PDF, and prove nothing was lost doing it.

Three steps, one command:

  1. Build a print copy of the page. The source is never touched. The copy is
     generated every run, so the two cannot drift by hand.
  2. Render it with headless Chrome.
  3. Verify: text diff against the source, embedded fonts, page count, page 1
     as a PNG to look at.

Usage:
  html_to_pdf.py SOURCE.html
  html_to_pdf.py SOURCE.html --out-dir writeup
  html_to_pdf.py SOURCE.html --reword "Click any of them to see::One line each"
  html_to_pdf.py SOURCE.html --keep-css        # keep the page's own look

Exit code is 1 when a check fails hard (fonts missing, render failed, a --reword
string not found). Soft findings are printed and do not fail the run.
"""
import argparse
import difflib
import html
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSS = os.path.join(os.path.dirname(HERE), "PRINT.css")

CHROME_NAMES = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]
# macOS keeps Chrome in /Applications, off PATH.
CHROME_APPS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
]

# Words that promise something a sheet of paper cannot do. Reported, never
# rewritten on their own -- rewording someone's prose is a decision, not a step.
SCREEN_WORDS = ["click", "hover", "scroll", "tap ", "expand", "collapse",
                "toggle", "pick an", "select the", "drop-down", "dropdown"]


def die(msg):
    print(f"FAIL  {msg}", file=sys.stderr)
    sys.exit(1)


def find_chrome():
    """CHROME env var first, then PATH, then the macOS app bundles."""
    env = os.environ.get("CHROME")
    if env:
        if os.path.isfile(env) and os.access(env, os.X_OK):
            return env, "CHROME=" + os.path.basename(env)
        die(f"CHROME={env!r} is not an executable file")
    for name in CHROME_NAMES:
        path = shutil.which(name)
        if path:
            return path, name
    for path in CHROME_APPS:
        if os.path.isfile(path):
            return path, os.path.basename(path)
    die("no Chrome or Chromium found. Looked on PATH for " + ", ".join(CHROME_NAMES)
        + " and at " + ", ".join(CHROME_APPS) + ". Set CHROME=/path/to/binary to point at one.")


def visible_text(markup):
    """What a reader actually sees. Style, script and nav never reach the page."""
    s = re.sub(r"<style.*?</style>|<script.*?</script>|<nav.*?</nav>|<div class=\"nav\">.*?</div>",
               " ", markup, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"<!--.*?-->", " ", s, flags=re.DOTALL)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


# --------------------------------------------------------------------------
# build the print copy
# --------------------------------------------------------------------------
def build(src_html, css, rewords, keep_css):
    notes = []

    title = "Document"
    mt = re.search(r"<title>(.*?)</title>", src_html, re.DOTALL | re.IGNORECASE)
    if mt:
        title = mt.group(1).strip()

    # Body: prefer <main>, else <body>. A page wrapper with a sidebar rail lives
    # outside <main>, and taking <main> drops it for free.
    mb = re.search(r"<main[^>]*>(.*)</main>", src_html, re.DOTALL | re.IGNORECASE)
    if mb:
        body = mb.group(1)
        notes.append("body taken from <main>")
    else:
        mb = re.search(r"<body[^>]*>(.*)</body>", src_html, re.DOTALL | re.IGNORECASE)
        if not mb:
            die("no <body> or <main> in the source")
        body = mb.group(1)
        notes.append("body taken from <body>")

    # 1. A jump rail is a scroll affordance. Paper has page numbers instead.
    body, n = re.subn(r"<nav\b.*?</nav>\s*", "", body, flags=re.DOTALL | re.IGNORECASE)
    body, n2 = re.subn(r'<div class="nav">.*?</div>\s*', "", body, flags=re.DOTALL | re.IGNORECASE)
    if n or n2:
        notes.append(f"removed {n + n2} nav block(s)")

    # 2. Scripts do nothing in a PDF.
    body, n = re.subn(r"<script\b.*?</script>\s*", "", body, flags=re.DOTALL | re.IGNORECASE)
    if n:
        notes.append(f"removed {n} script block(s)")

    # 3. A tab picker hides every panel but one. Promote each tab's label to a
    #    heading on its panel and stack them, or the PDF quietly ships one of
    #    five. Matched on the button's data-* value against the panel id.
    tabs_html = ""
    mtabs = re.search(r'<div class="tabs">(.*?)</div>\s*', body, re.DOTALL)
    if mtabs:
        tabs_html = mtabs.group(1)
        pairs = re.findall(r'data-[a-z-]+="([^"]+)"[^>]*>(?:\s*<[^>]+>\s*)*([^<]+)</button>', tabs_html)
        labels = {k: v.strip() for k, v in pairs}
        promoted = 0
        if labels:
            def promote(mo):
                nonlocal promoted
                cls, pid = mo.group(1), mo.group(2)
                key = next((k for k in labels if pid.endswith(k)), None)
                if not key:
                    return mo.group(0)
                promoted += 1
                name = html.escape(labels[key])
                cls = cls.replace(" on", "")
                return (f'<div class="{cls}" id="{pid}">\n'
                        f'    <h3 class="print-tab-name">{name}</h3>')
            body = re.sub(r'<div class="(panel[^"]*)" id="([^"]+)">', promote, body)
        if promoted:
            body = body.replace(mtabs.group(0), "")
            notes.append(f"unstacked {promoted} tab panel(s), labels promoted to headings")
        else:
            notes.append("WARN a .tabs picker was found but no panel matched it -- left alone")

    # 4. A collapsed <details> prints as its summary line only. Everything inside
    #    it disappears and the page still looks finished.
    body, n_det = re.subn(r"<details(?![^>]*\bopen\b)", "<details open", body, flags=re.IGNORECASE)
    if n_det:
        notes.append(f"opened {n_det} <details>")

    # 5. Buttons are all screen: theme toggles, copy buttons, tab remnants.
    body, n_btn = re.subn(r"<button\b.*?</button>\s*", "", body, flags=re.DOTALL | re.IGNORECASE)
    if n_btn:
        notes.append(f"removed {n_btn} button(s)")

    # 6. Caller-supplied rewordings, applied to the print copy only.
    for pair in rewords:
        if "::" not in pair:
            die(f'--reword needs "was::now", got {pair!r}')
        was, now = pair.split("::", 1)
        if was not in body:
            die(f"--reword string not found in the page: {was!r}")
        body = body.replace(was, now)
    if rewords:
        notes.append(f"applied {len(rewords)} rewording(s)")

    if keep_css:
        styles = "\n".join(m.group(0) for m in re.finditer(r"<style.*?</style>", src_html, re.DOTALL | re.IGNORECASE))
        head = styles + "\n<style>\n" + print_only_block() + "</style>"
        root_attr = ' data-theme="light"'
        notes.append("kept the page's own CSS, forced data-theme=light")
    else:
        head = "<style>\n" + css + "</style>"
        root_attr = ""

    page = (f'<!doctype html>\n<html lang="en"{root_attr}>\n<head>\n'
            f'<meta charset="utf-8" />\n<title>{title}</title>\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1" />\n'
            f"{head}\n</head>\n<body>\n<div class=\"wrap\">\n{body}\n</div>\n</body>\n</html>\n")
    return page, notes


def print_only_block():
    """Just the paper rules, for when the page keeps its own look."""
    return ("@page{margin:14mm}\n"
            "@media print{*{-webkit-print-color-adjust:exact;print-color-adjust:exact}\n"
            "tr{break-inside:avoid}\n"
            "table,details,section{break-inside:auto}\n"
            "pre,.chip,.tag,.card,.step,.note-line{break-inside:avoid}\n"
            "h1,h2,h3{break-after:avoid}\n"
            "a{text-decoration:none}}\n")


# --------------------------------------------------------------------------
# verify
# --------------------------------------------------------------------------
def report_text_diff(src_html, print_html):
    a, b = visible_text(src_html).split(), visible_text(print_html).split()
    sm = difflib.SequenceMatcher(None, a, b)
    ratio = sm.ratio() * 100
    print(f"\n  text kept          {ratio:.2f}%  ({len(a)} words in, {len(b)} out)")
    changes = [op for op in sm.get_opcodes() if op[0] != "equal"]
    if not changes:
        print("  every word survived")
    for tag, i1, i2, j1, j2 in changes[:12]:
        if tag in ("delete", "replace"):
            print(f"    gone   {' '.join(a[i1:i2])[:88]!r}")
        if tag in ("insert", "replace"):
            print(f"    added  {' '.join(b[j1:j2])[:88]!r}")
    if len(changes) > 12:
        print(f"    ... and {len(changes) - 12} more")
    print("  Every line above has to be an expected transform. Anything else is lost content.")
    return ratio


def report_screen_words(print_html):
    text = visible_text(print_html).lower()
    hits = [(w, text.count(w)) for w in SCREEN_WORDS if w in text]
    if not hits:
        print("\n  screen-only wording   none found")
        return
    print("\n  screen-only wording   " + ", ".join(f"{w.strip()} x{c}" for w, c in hits))
    print("  Nobody clicks a printout. Reword with --reword \"was::now\", or leave it if it reads fine.")


def report_fonts(pdf, house_sheet):
    if not shutil.which("pdffonts"):
        print("\n  fonts              pdffonts not installed, not checked")
        return
    out = subprocess.run(["pdffonts", pdf], capture_output=True, text=True).stdout
    fams = sorted({re.sub(r"^[A-Z]{6}\+", "", ln.split()[0]) for ln in out.splitlines()[2:] if ln.split()})
    print(f"\n  fonts embedded     {', '.join(fams) if fams else 'NONE'}")
    if not house_sheet:
        # --keep-css means the page brought its own look, house fonts and all.
        print("  (--keep-css, so the page's own fonts are the right answer)")
        return
    missing = [f for f in ("Manrope", "JetBrains") if not any(f in x for x in fams)]
    if missing:
        print(f"  FAIL missing {', '.join(missing)} -- the page fell back to a system font")
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="the HTML page to convert")
    ap.add_argument("--out-dir", help="where the pdf goes (default: beside the source)")
    ap.add_argument("--css", default=DEFAULT_CSS, help="stylesheet to use (default: the skill's PRINT.css)")
    ap.add_argument("--reword", action="append", default=[], metavar='"was::now"',
                    help="replace a string in the print copy only. Repeatable.")
    ap.add_argument("--keep-css", action="store_true",
                    help="keep the page's own styling instead of the house sheet")
    args = ap.parse_args()

    src = os.path.abspath(args.source)
    if not os.path.exists(src):
        die(f"no such file: {src}")
    out_dir = os.path.abspath(args.out_dir) if args.out_dir else os.path.dirname(src)
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(src))[0]
    print_path = os.path.join(out_dir, f"{stem}-print.html")
    pdf_path = os.path.join(out_dir, f"{stem}.pdf")
    png_stem = os.path.join(out_dir, f"{stem}-page1")

    if os.path.abspath(print_path) == src:
        die("the source is already a -print.html file, refusing to overwrite it")

    src_html = open(src, encoding="utf-8").read()
    css = "" if args.keep_css else open(args.css, encoding="utf-8").read()
    if not args.keep_css and "@font-face" not in css:
        die(f"{args.css} has no @font-face -- the PDF would fall back to a system font")

    page, notes = build(src_html, css, args.reword, args.keep_css)
    open(print_path, "w", encoding="utf-8").write(page)

    print("BUILD")
    for n in notes:
        print(f"  {n}")
    print(f"  wrote {print_path}  ({len(page):,} bytes)")

    chrome, chrome_name = find_chrome()
    cmd = [chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer",
           f"--print-to-pdf={pdf_path}", f"file://{print_path}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) < 1000:
        # Some sandboxes need it off. Say so rather than failing silently.
        r = subprocess.run(cmd[:1] + ["--no-sandbox"] + cmd[1:], capture_output=True, text=True)
        if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) < 1000:
            die(f"{chrome_name} produced no PDF:\n{r.stderr[-800:]}")
        print("  (needed --no-sandbox)")

    print(f"\nRENDER\n  {chrome_name}  ->  {pdf_path}  ({os.path.getsize(pdf_path):,} bytes)")
    if open(pdf_path, "rb").read(5) != b"%PDF-":
        die("output is not a PDF")

    pages = ""
    if shutil.which("pdfinfo"):
        info = subprocess.run(["pdfinfo", pdf_path], capture_output=True, text=True).stdout
        pages = next((l.split(":", 1)[1].strip() for l in info.splitlines() if l.startswith("Pages")), "?")
        size = next((l.split(":", 1)[1].strip() for l in info.splitlines() if l.startswith("Page size")), "?")
        print(f"  {pages} pages, {size}")

    print("\nVERIFY")
    ratio = report_text_diff(src_html, page)
    report_screen_words(page)
    fonts_ok = report_fonts(pdf_path, house_sheet=not args.keep_css)

    if shutil.which("pdftoppm"):
        subprocess.run(["pdftoppm", "-png", "-r", "70", "-f", "1", "-l", "1", pdf_path, png_stem],
                       capture_output=True)
        png = png_stem + "-1.png" if os.path.exists(png_stem + "-1.png") else png_stem + "-01.png"
        if os.path.exists(png):
            print(f"\n  page 1             {png}")
            print("  Open it. A PDF missing half its content looks exactly like one that is not.")
    else:
        print("\n  page 1             pdftoppm not installed, no image to check")

    print(f"\nDONE  {pdf_path}")
    if fonts_ok is False:
        sys.exit(1)
    if ratio < 95:
        print(f"\nWARN  only {ratio:.2f}% of the text survived. Read the diff above before shipping this.")


if __name__ == "__main__":
    main()
