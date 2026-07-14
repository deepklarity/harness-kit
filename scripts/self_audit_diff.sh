#!/bin/sh
# scripts/self_audit_diff.sh — flag duplicate definitions and commented-out
# dead code in a git diff. The mechanical assist for the pre-completion
# self-audit gate injected into the odin worker prompt.
#
# Born from the task-170 forensic audit: an agent shipped triplicate copies of
# an email-to-agent parser (two `def _extract_agent(` in one file) plus ~90
# lines of commented-out `contextStats` dead code. Both escaped into review and
# cost a full rework round. This script moves the check LEFT of the SUCCESS
# emission so the agent catches its own slop before declaring done.
#
# Usage:
#   scripts/self_audit_diff.sh                 # working-tree diff (HEAD..working)
#   scripts/self_audit_diff.sh --staged        # staged only (HEAD..index)
#   scripts/self_audit_diff.sh <commit>        # single commit (commit^..commit)
#   scripts/self_audit_diff.sh <a>..<b>        # explicit range
#
# For every code file touched by the diff, the script scans the file's FINAL
# state (after the change is applied) for two defect classes:
#
#   1. Duplicate definitions — a function/class/method name defined more than
#      once in the same file. Two `def foo(` lines silently shadow each other;
#      the second wins and the first becomes dead code. This is almost always a
#      bug, so any hit fails the gate.
#   2. Commented-out dead code — runs of ≥ $MIN_BLOCK_SIZE (default 5)
#      consecutive commented lines that look like executable code (def/return/
#      const/=>/useMemo…). Small legit comments and JSDoc stay under the
#      threshold; only substantial dead-code regions are flagged.
#
# "Files you touch, you own" — the audit runs on the final state of every file
# changed by the diff, not just the added lines, because a duplicate or dead
# block anywhere in a file you're shipping is your slop to catch now.
#
# Exit codes: 0 = clean; 1 = issues found; 2 = usage/git error.
# Last stdout line is machine-scannable:
#   SELF_AUDIT: duplicates=N commented_blocks=N files=M verdict=<clean|issues>

set -u

MIN_BLOCK_SIZE=${SELFAUDIT_MIN_BLOCK_SIZE:-5}

usage() {
    cat <<'EOF' >&2
Usage: scripts/self_audit_diff.sh [--staged | <commit> | <a>..<b>]

Audits a git diff for duplicate definitions and commented-out dead code.
No argument audits the working tree (staged + unstaged) against HEAD.

Exit: 0 clean, 1 issues found, 2 usage/git error.
EOF
}

# ── arg parsing ────────────────────────────────────────────────────────
STAGED=0
DIFF_SPEC="HEAD"
TREE_REF=""      # empty → read working-tree files directly

if [ $# -gt 1 ]; then
    usage
    exit 2
fi

ARG="${1:-}"
case "$ARG" in
    -h|--help)
        usage
        exit 0
        ;;
    --staged)
        STAGED=1
        DIFF_SPEC="--cached HEAD"
        TREE_REF=""
        ;;
    "")
        DIFF_SPEC="HEAD"
        TREE_REF=""
        ;;
    *..*)
        DIFF_SPEC="$ARG"
        TREE_REF="${ARG#*..}"    # right-hand side = the "after" state
        ;;
    *)
        DIFF_SPEC="${ARG}^..${ARG}"
        TREE_REF="$ARG"
        ;;
esac

# ── environment checks ─────────────────────────────────────────────────
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    printf 'self_audit: not inside a git work tree\n' >&2
    exit 2
fi

# Validate any explicit ref resolves (catches typos / bad replays).
if [ -n "$TREE_REF" ] && [ "$STAGED" -eq 0 ]; then
    if ! git rev-parse --verify "$TREE_REF^{commit}" >/dev/null 2>&1; then
        printf 'self_audit: cannot resolve ref %s\n' "$TREE_REF" >&2
        exit 2
    fi
fi

# ── gather the changed file list ───────────────────────────────────────
if [ "$STAGED" -eq 1 ]; then
    CHANGED=$(git diff --cached --name-only --diff-filter=d HEAD 2>/dev/null || true)
elif [ -n "$TREE_REF" ]; then
    CHANGED=$(git diff --name-only --diff-filter=d "$DIFF_SPEC" 2>/dev/null || true)
else
    CHANGED=$(git diff --name-only --diff-filter=d HEAD 2>/dev/null || true)
fi

if [ -z "$CHANGED" ]; then
    printf 'self_audit: no changed files in %s\n' "$DIFF_SPEC" >&2
    printf 'SELF_AUDIT: duplicates=0 commented_blocks=0 files=0 verdict=clean\n'
    exit 0
fi

# ── helpers ────────────────────────────────────────────────────────────

# Print a file's final-state content. Working-tree mode reads the file
# directly; commit/range mode reads from the resolved tree.
read_file() {
    if [ -n "$TREE_REF" ]; then
        git show "${TREE_REF}:$1" 2>/dev/null
    else
        cat "$1" 2>/dev/null
    fi
}

# Language tag from extension, or "" for non-code files.
lang_of() {
    case "$1" in
        *.py) printf 'py' ;;
        *.js|*.jsx|*.mjs|*.cjs|*.ts|*.tsx|*.mts|*.cts) printf 'js' ;;
        *) printf '' ;;
    esac
}

# Count non-empty lines on stdin (awk always exits 0 and prints a number).
count_lines() {
    awk 'NF { c++ } END { print c + 0 }'
}

# Duplicate definitions in a Python file: any MODULE-LEVEL def/class name
# defined more than once (column 0). Two `def foo(` at module scope silently
# shadow each other — the second wins, the first becomes dead code. Class
# methods are intentionally excluded: sharing names across classes (overriding
# setUp, get_queryset, etc.) is legitimate, and flagging it drowns the signal.
py_duplicates() {
    read_file "$1" | grep -oE '^(async[[:space:]]+)?(def|class)[[:space:]]+[A-Za-z_][A-Za-z0-9_]*' \
        | sed -E 's/.*(def|class)[[:space:]]+//' \
        | sort | uniq -d
}

# Duplicate definitions in a JS/TS file: top-level function declarations
# sharing a name. (const/let are excluded — shadowing across scopes is legal
# and common; flagging it produces only noise.)
js_duplicates() {
    read_file "$1" | grep -oE '^(export[[:space:]]+)?(async[[:space:]]+)?function[[:space:]]+[A-Za-z_$][A-Za-z0-9_$]*' \
        | sed -E 's/.*function[[:space:]]+//' \
        | sort | uniq -d
}

# Commented-out dead-code BLOCKS in a Python file: runs of ≥ MIN consecutive
# `#`-comment lines that read like executable code. Returns the block count.
py_comment_blocks() {
    read_file "$1" | awk -v MIN="$MIN_BLOCK_SIZE" '
        BEGIN { run = 0; blocks = 0 }
        {
            is_cc = 0
            if ($0 ~ /^[[:space:]]*#/) {
                s = $0; sub(/^[[:space:]]*#[[:space:]]?/, "", s)
                if (s ~ /^(def |class |async def |return |yield |raise |import |from |if |for |while |elif |else:|try:|except |with |print\(|assert )/ \
                    || s ~ /^[A-Za-z_][A-Za-z0-9_]*[[:space:]]*=[^=]/) {
                    is_cc = 1
                }
            }
            if (is_cc) { run++ }
            else { if (run >= MIN) blocks++; run = 0 }
        }
        END { if (run >= MIN) blocks++; print blocks + 0 }
    '
}

# Commented-out dead-code BLOCKS in a JS/TS file: /* */ regions (or // runs)
# containing ≥ MIN lines of executable tokens. JSDoc and small legit comments
# stay under the threshold.
js_comment_blocks() {
    read_file "$1" | awk -v MIN="$MIN_BLOCK_SIZE" '
        BEGIN { in_block = 0; block_code = 0; blocks = 0; run = 0 }
        {
            line = $0
            if (in_block) {
                if (line ~ /(const|let|function|=>|return[[:space:]]*\{|useState|useMemo|useEffect|import[[:space:]]|export[[:space:]]|\.map\(|\.filter\(|\.reduce\()/) block_code++
                if (line ~ /\*\//) { in_block = 0; if (block_code >= MIN) blocks++; block_code = 0 }
                next
            }
            if (line ~ /\/\*/ && line !~ /\*\//) {
                if (run >= MIN) blocks++; run = 0
                if (line ~ /(const|let|function|=>|return[[:space:]]*\{|useState|useMemo|useEffect|import[[:space:]]|export[[:space:]]|\.map\(|\.filter\(|\.reduce\()/) block_code++
                in_block = 1
                next
            }
            if (line ~ /\/\*.*\*\//) {
                if (run >= MIN) blocks++; run = 0
                next
            }
            if (line ~ /^[[:space:]]*\/\//) {
                if (line ~ /(const|let|function|=>|return[[:space:]]*\{|useState|useMemo|useEffect)/) { run++ }
                else { if (run >= MIN) blocks++; run = 0 }
            } else {
                if (run >= MIN) blocks++; run = 0
            }
        }
        END { if (in_block && block_code >= MIN) blocks++; if (run >= MIN) blocks++; print blocks + 0 }
    '
}

# ── scan (single pass, main-shell accumulation) ────────────────────────
TOTAL_DUPES=0
TOTAL_BLOCKS=0
FILE_COUNT=0
REPORT=""
NL="
"

# Iterate the changed list in the main shell so accumulators persist.
FILE_LIST=$(mktemp)
printf '%s\n' "$CHANGED" > "$FILE_LIST"
while read -r f; do
    [ -z "$f" ] && continue
    lang=$(lang_of "$f")
    [ -z "$lang" ] && continue
    FILE_COUNT=$((FILE_COUNT + 1))

    dupes=""
    blocks=0
    if [ "$lang" = "py" ]; then
        dupes=$(py_duplicates "$f")
        blocks=$(py_comment_blocks "$f")
    else
        dupes=$(js_duplicates "$f")
        blocks=$(js_comment_blocks "$f")
    fi

    ndupes=0
    if [ -n "$dupes" ]; then
        ndupes=$(printf '%s\n' "$dupes" | count_lines)
        joined=$(printf '%s\n' "$dupes" | paste -sd, -)
        REPORT="${REPORT}DUPLICATE ${f}: ${joined} (${ndupes} name(s))${NL}"
    fi
    if [ "$blocks" -gt 0 ]; then
        REPORT="${REPORT}COMMENTED_CODE ${f}: ${blocks} block(s) of dead code${NL}"
    fi
    TOTAL_DUPES=$((TOTAL_DUPES + ndupes))
    TOTAL_BLOCKS=$((TOTAL_BLOCKS + blocks))
done < "$FILE_LIST"
rm -f "$FILE_LIST"

# ── report ─────────────────────────────────────────────────────────────
printf 'self_audit: auditing %s (min block size %s)\n' "$DIFF_SPEC" "$MIN_BLOCK_SIZE" >&2

if [ -n "$REPORT" ]; then
    printf '%s' "$REPORT"
fi

if [ "$TOTAL_DUPES" -gt 0 ] || [ "$TOTAL_BLOCKS" -gt 0 ]; then
    verdict="issues"
    code=1
else
    verdict="clean"
    code=0
fi

printf 'SELF_AUDIT: duplicates=%s commented_blocks=%s files=%s verdict=%s\n' \
    "$TOTAL_DUPES" "$TOTAL_BLOCKS" "$FILE_COUNT" "$verdict"
exit "$code"
