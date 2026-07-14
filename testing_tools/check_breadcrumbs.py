#!/usr/bin/env python3
import os
import re
import sys
import glob

# ANSI colors for nice output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

SYMBOL_LABELS = {
    "function", "functions", "class", "symbol", "method", "abstract method", "handler"
}

def is_path_candidate(s):
    s = s.strip()
    if not s or " " in s:
        return False
    # Strip line number suffix if any
    if ":" in s:
        parts = s.split(":")
        if parts[1].isdigit() or "-" in parts[1]:
            s = parts[0]
            
    # Exclude directory-only .odin paths
    if s.startswith(".odin/") and not any(s.endswith(ext) for ext in [".yaml", ".json", ".toml", ".yml"]):
        return False
    # Exclude API endpoints/routes
    if "{" in s or "}" in s or "/:" in s:
        return False
    if s.startswith("/") and not any(s.endswith(ext) for ext in [".py", ".ts", ".tsx", ".json", ".md", ".yaml", ".sh", ".css"]):
        return False
    # If it contains / or ends with typical extensions
    if "/" in s:
        return True
    extensions = [".py", ".ts", ".tsx", ".json", ".md", ".yaml", ".sh", ".css"]
    if any(s.endswith(ext) for ext in extensions):
        return True
    return False

def is_symbol_candidate(s):
    s = s.strip()
    if "(" in s:
        s = s.split("(", 1)[0].strip()
    if not s:
        return False
    return bool(re.match(r"^[a-zA-Z_][a-zA-Z0-9_\.]*$", s))

def resolve_path(repo_root, path_str):
    path_str = path_str.strip()
    # Strip line suffix
    if ":" in path_str:
        parts = path_str.split(":")
        if parts[1].isdigit() or "-" in parts[1]:
            path_str = parts[0]

    # Handle wildcards/placeholders
    if "<" in path_str or ">" in path_str or "*" in path_str:
        parent_dir = os.path.dirname(path_str)
        abs_parent = os.path.join(repo_root, parent_dir)
        if os.path.isdir(abs_parent):
            return path_str, True
        return path_str, False

    # Check directly as-is
    abs_path = os.path.join(repo_root, path_str)
    if os.path.exists(abs_path):
        return path_str, True
        
    # Handle common shortened prefixes
    if path_str.startswith("tasks/"):
        candidate = os.path.join("taskit", "taskit-backend", path_str)
        if os.path.exists(os.path.join(repo_root, candidate)):
            return candidate, True
            
    if path_str.startswith("src/"):
        candidate = os.path.join("taskit", "taskit-frontend", path_str)
        if os.path.exists(os.path.join(repo_root, candidate)):
            return candidate, True
            
    if path_str.startswith("config/"):
        candidate = os.path.join("taskit", "taskit-backend", path_str)
        if os.path.exists(os.path.join(repo_root, candidate)):
            return candidate, True

    # Fallback/search: find files matching this suffix strictly as path components
    matches = []
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "venv", "dist", "build", "htmlcov")]
        for file in files:
            full_rel = os.path.relpath(os.path.join(root, file), repo_root)
            if full_rel == path_str or full_rel.endswith("/" + path_str):
                matches.append(full_rel)
    
    if len(matches) == 1:
        return matches[0], True
        
    return path_str, False

def check_symbol_in_file(abs_file_path, symbol):
    if not os.path.exists(abs_file_path):
        return False
        
    try:
        with open(abs_file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception:
        return False
        
    clean_sym = symbol.strip()
    if "(" in clean_sym:
        clean_sym = clean_sym.split("(", 1)[0].strip()
        
    parts = [p.strip() for p in clean_sym.split(".") if p.strip()]
    if not parts:
        return False
        
    for part in parts:
        if not re.search(r"\b" + re.escape(part) + r"\b", content):
            return False
            
    return True

def main():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    details_pattern = os.path.join(repo_root, "docs/breadcrumb_analysis/**/DETAILS.md")
    details_files = glob.glob(details_pattern, recursive=True)
    
    if not details_files:
        print(f"{RED}No DETAILS.md files found in docs/breadcrumb_analysis/{RESET}")
        sys.exit(1)
        
    total_checked = 0
    total_drifted = 0
    drift_by_breadcrumb = {}
    
    for df in sorted(details_files):
        rel_df = os.path.relpath(df, repo_root)
        breadcrumb_name = os.path.dirname(os.path.relpath(df, os.path.join(repo_root, "docs/breadcrumb_analysis")))
        
        drift_by_breadcrumb[breadcrumb_name] = []
        
        with open(df, "r", encoding="utf-8") as f:
            content = f.read()
            lines = content.splitlines()
            
        # Check if the breadcrumb is proposed
        is_proposed = "status: proposed" in content.lower()
        
        current_file = None
        
        for line_idx, line in enumerate(lines, 1):
            m = re.match(r"^\s*\*\*(.*?)\*\*:\s*(.*)", line)
            if not m:
                continue
                
            label, rest = m.groups()
            label_clean = label.strip().lower()
            backticks = re.findall(r"`([^`]+)`", rest)
            if not backticks:
                continue
                
            # Parse references from backticks on this line
            refs = [] # List of (path, symbol)
            
            i = 0
            while i < len(backticks):
                b = backticks[i]
                if "::" in b:
                    parts = b.split("::", 1)
                    refs.append((parts[0].strip(), parts[1].strip()))
                    i += 1
                elif is_path_candidate(b):
                    # Check if the next block is a symbol (not a path candidate)
                    symbol = None
                    if i + 1 < len(backticks):
                        next_b = backticks[i + 1]
                        if not ("::" in next_b) and not is_path_candidate(next_b) and is_symbol_candidate(next_b):
                            symbol = next_b
                            i += 1 # Consume the symbol block
                    refs.append((b.strip(), symbol))
                    i += 1
                else:
                    i += 1
                    
            for path_val, sym_val in refs:
                total_checked += 1
                
                # Split line numbers if any
                path_part = path_val
                if ":" in path_val:
                    sp = path_val.split(":")
                    if sp[1].isdigit() or "-" in sp[1]:
                        path_part = sp[0]
                        
                resolved_path, exists = resolve_path(repo_root, path_part)
                if not exists:
                    drift_by_breadcrumb[breadcrumb_name].append({
                        "line_num": line_idx,
                        "line_content": line.strip(),
                        "path": path_val,
                        "reason": f"File path does not exist (tried to resolve to: {resolved_path})"
                    })
                    total_drifted += 1
                    continue
                
                # Update current file context
                current_file = resolved_path
                    
                # Symbol checking (skipped if breadcrumb is proposed)
                if sym_val and not is_proposed:
                    if "<" in resolved_path or ">" in resolved_path or "*" in resolved_path:
                        # Skip placeholder files
                        pass
                    else:
                        abs_resolved_path = os.path.join(repo_root, resolved_path)
                        has_sym = check_symbol_in_file(abs_resolved_path, sym_val)
                        if not has_sym:
                            drift_by_breadcrumb[breadcrumb_name].append({
                                "line_num": line_idx,
                                "line_content": line.strip(),
                                "path": resolved_path,
                                "symbol": sym_val,
                                "reason": f"Symbol `{sym_val}` not found in file `{resolved_path}`"
                            })
                            total_drifted += 1
                            
            # Standalone symbol checking
            if not refs and label_clean in SYMBOL_LABELS and current_file:
                if "<" in current_file or ">" in current_file or "*" in current_file:
                    # Skip placeholder files
                    pass
                else:
                    for b in backticks:
                        if is_symbol_candidate(b) and not is_proposed:
                            total_checked += 1
                            abs_resolved_path = os.path.join(repo_root, current_file)
                            has_sym = check_symbol_in_file(abs_resolved_path, b)
                            if not has_sym:
                                drift_by_breadcrumb[breadcrumb_name].append({
                                    "line_num": line_idx,
                                    "line_content": line.strip(),
                                    "path": current_file,
                                    "symbol": b,
                                    "reason": f"Symbol `{b}` not found in file `{current_file}`"
                                })
                                total_drifted += 1

    # Print results
    print("=" * 80)
    print(f"BREADCRUMB DRIFT AUDIT REPORT")
    print("=" * 80)
    
    has_drift = False
    for breadcrumb, drifts in drift_by_breadcrumb.items():
        if drifts:
            has_drift = True
            print(f"\n{RED}✗ Breadcrumb: {breadcrumb} ({len(drifts)} drift(s) found){RESET}")
            for d in drifts:
                print(f"  Line {d['line_num']}: {d['line_content']}")
                print(f"    {YELLOW}Error: {d['reason']}{RESET}")
        else:
            print(f"{GREEN}✓ Breadcrumb: {breadcrumb} (clean){RESET}")
            
    print("\n" + "=" * 80)
    print(f"Summary: {total_checked} references checked. {total_drifted} drift(s) detected.")
    print("=" * 80)
    
    if has_drift:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()
