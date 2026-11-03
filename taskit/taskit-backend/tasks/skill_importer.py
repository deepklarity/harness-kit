"""Skill Importer & Relevance Classifier Service.

Provides functionality to:
- Discover and parse skills installed in ``.claude/skills/``.
- Classify skills into 4 target tiers:
  - 'task': Discrete executable tasks & audits (Task Presets).
  - 'spec': Quality directives & execution rules (Spec Presets).
  - 'both': Applicable to both Task Presets and Spec Directives.
  - 'none': Backend / internal agent skills (hidden from UI presets).
- Import skills from Git URLs, local directory paths, raw file URLs, or zip archives.
- Handle duplicate skill detection (skips existing skills cleanly).
- Delete installed skills from disk.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)

MAX_IMPORT_FILE_SIZE_BYTES = 15 * 1024 * 1024  # 15 MB cap


def sanitize_skill_name(name: str) -> str:
    """Sanitize skill directory name to prevent directory traversal outside .claude/skills/."""
    if not name:
        return "imported-skill"
    clean_name = os.path.basename(name.strip())
    clean_name = re.sub(r"[^a-zA-Z0-9_\-]", "-", clean_name)
    clean_name = re.sub(r"-+", "-", clean_name).strip("-")
    return clean_name or "imported-skill"


def safe_extract_zip(zip_file: zipfile.ZipFile, target_dir: Path) -> None:
    """Safely extract zip archive members ensuring no entry escapes target_dir (Zip Slip prevention)."""
    target_dir_resolved = target_dir.resolve()
    for member in zip_file.infolist():
        if member.filename.startswith(".git/") or "/.git/" in member.filename:
            continue
        member_path = (target_dir / member.filename).resolve()
        try:
            if not member_path.is_relative_to(target_dir_resolved):
                raise ValueError(f"Security Exception: Zip member '{member.filename}' attempts path traversal outside destination.")
        except AttributeError:
            if not str(member_path).startswith(str(target_dir_resolved)):
                raise ValueError(f"Security Exception: Zip member '{member.filename}' attempts path traversal outside destination.")
    zip_file.extractall(target_dir)


def safe_extract_tar(tar_file: Any, target_dir: Path) -> None:
    """Safely extract tar archive members ensuring no entry escapes target_dir (Zip Slip prevention)."""
    target_dir_resolved = target_dir.resolve()
    for member in tar_file.getmembers():
        if member.name.startswith(".git/") or "/.git/" in member.name:
            continue
        member_path = (target_dir / member.name).resolve()
        try:
            if not member_path.is_relative_to(target_dir_resolved):
                raise ValueError(f"Security Exception: Tar member '{member.name}' attempts path traversal outside destination.")
        except AttributeError:
            if not str(member_path).startswith(str(target_dir_resolved)):
                raise ValueError(f"Security Exception: Tar member '{member.name}' attempts path traversal outside destination.")
    tar_file.extractall(target_dir)

# Keywords suggesting a skill is relevant as a Spec Directive
SPEC_KEYWORDS = {
    "prevent", "prevention", "discipline", "directive", "guideline",
    "rule", "enforce", "code-quality", "spec", "standard", "guardrail",
    "must", "prohibit", "avoid", "hygiene", "quality", "clean"
}

# Keywords suggesting a skill is a Task / Audit / Actionable Workflow
TASK_KEYWORDS = {
    "audit", "review", "report", "generate", "create", "build",
    "scaffold", "setup", "triage", "migration", "diagnose", "analysis",
    "investigate", "explore", "benchmark", "validation", "overview"
}

# Keywords for internal workflow / subagent helper skills (backend only)
BACKEND_ONLY_KEYWORDS = {
    "handoff", "breadcrumb", "breadcrumbs", "subagent", "internal",
    "wait-what", "writing-beats", "writing-fragments", "writing-shape",
    "local-logs", "ask-matt", "follow-breadcrumb", "scaffold-exercises"
}


def get_skills_dir() -> Path:
    """Return project root's ``.claude/skills`` directory path."""
    from .audit_presets import _default_path
    try:
        preset_file = _default_path()
        return preset_file.resolve().parents[3] / ".claude" / "skills"
    except Exception:
        return Path(__file__).resolve().parents[3] / ".claude" / "skills"


def parse_skill_md(file_path: Path) -> dict[str, Any]:
    """Parse a SKILL.md file into frontmatter dict and body text."""
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed reading %s: %s", file_path, exc)
        return {"name": file_path.parent.name, "description": "", "body": "", "frontmatter": {}}

    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {
            "name": file_path.parent.name,
            "title": file_path.parent.name.replace("hk-", "").replace("-", " ").title(),
            "description": text[:200].strip(),
            "body": text,
            "frontmatter": {},
        }

    fm_text, body = m.groups()
    try:
        frontmatter = yaml.safe_load(fm_text) or {}
    except Exception as exc:
        logger.warning("Failed parsing YAML frontmatter in %s: %s", file_path, exc)
        frontmatter = {}

    name = frontmatter.get("name") or file_path.parent.name
    description = frontmatter.get("description") or ""

    # Extract title from first H1 if present
    title = ""
    for line in body.splitlines():
        line_s = line.strip()
        if line_s.startswith("# "):
            title = line_s[2:].strip()
            break

    if not title:
        raw_name = name.replace("hk-", "")
        title = raw_name.replace("-", " ").title()

    parsed = {
        "name": name,
        "title": title,
        "description": description,
        "body": body.strip(),
        "frontmatter": frontmatter,
        "path": str(file_path),
    }
    return _follow_stub(parsed, file_path)


_STUB_RE = re.compile(r"This skill lives in `([^`]+)/`\. Read `[^`]+/SKILL\.md`")


def _follow_stub(parsed: dict[str, Any], file_path: Path) -> dict[str, Any]:
    """``.claude/skills/<name>/SKILL.md`` may be a stub pointing at ``docs/skills/<category>/<name>/``.

    The stub keeps the frontmatter Claude Code needs; the real body lives at the target. Return
    the target's body and title so scoring, categorisation and dependency detection see real content.
    """
    m = _STUB_RE.search(parsed.get("body", ""))
    if not m:
        return parsed
    root = file_path.resolve().parents[3] if len(file_path.resolve().parents) > 3 else file_path.parent
    target = root / m.group(1) / "SKILL.md"
    if not target.is_file() or target.resolve() == file_path.resolve():
        logger.warning("Skill stub %s points at missing %s", file_path, target)
        return parsed
    real = parse_skill_md(target)
    parsed["body"] = real["body"]
    parsed["title"] = real["title"]
    parsed["skill_dir"] = str(target.parent)
    return parsed


def classify_skill_relevance(skill_data: dict[str, Any]) -> dict[str, Any]:
    """Determine a skill's target tier: 'task', 'spec', 'both', or 'none' (backend only).

    Strict criteria:
    - Task Presets (task): ONLY standalone directory-level audits/inspections.
    - Spec Presets (spec): ONLY directives relevant to spec content and building features.
    - None (none): Interactive prompts, design sessions, triage, teaching, ticket generators.
    """
    fm = skill_data.get("frontmatter", {})
    name = (skill_data.get("name") or "").lower()
    title = (skill_data.get("title") or "").lower()
    description = (skill_data.get("description") or "").lower()

    # 1. Explicit frontmatter override
    explicit_target = fm.get("preset_target") or fm.get("preset_type")
    if explicit_target in ("task", "spec", "both", "none"):
        target = explicit_target
    elif fm.get("disable-model-invocation") is True:
        target = "none"
    else:
        combined = f"{name} {title} {description}"

        # Interactive prompts, design sessions, triage, teaching, ticket generators -> NOT task or spec presets
        is_interactive = any(kw in combined for kw in (
            "code-review", "codebase-design", "domain-modeling", "diagnosing-bugs",
            "grill", "grilling", "teach", "triage", "to-tickets", "to-spec", "to-questionnaire",
            "implement", "prototype", "ask-matt", "wait-what", "writing-for-agents",
            "writing-beats", "writing-fragments", "writing-shape", "scaffold-exercises",
            "handoff", "breadcrumb"
        ))

        # Check for strict directory-level audit task
        is_directory_audit = any(kw in combined for kw in (
            "ai-slop-detection", "slop-detection", "slop-review", "slop audit", "code-quality-audit",
            "architecture-review", "arch-audit", "security-audit", "performance-audit",
            "ui-gaps-analysis", "website-ux-audit", "web-app-ui-validation",
            "mobile-app-ui-validation", "test-case-audit", "hygiene-audit",
            "prompt-quality-audit", "prompt-comparison-audit", "roadmap-audit",
            "autolint", "forensic audit"
        ))

        # Check for strict spec-building directive
        is_spec_directive = any(kw in combined for kw in (
            "prevent-ai-slop", "prevent-frontend-slop", "prevent-backend-slop",
            "tdd-discipline", "deep-module-boundaries", "prevent slop",
            "clean code directive", "tdd rigor"
        ))

        if is_interactive:
            target = "none"
        elif is_directory_audit and is_spec_directive:
            target = "both"
        elif is_directory_audit:
            target = "task"
        elif is_spec_directive:
            target = "spec"
        else:
            target = "none"

    is_task_preset = target in ("task", "both")
    is_spec_preset = target in ("spec", "both")
    is_backend_only = target == "none"

    return {
        "preset_target": target,
        "is_task_preset": is_task_preset,
        "is_spec_preset": is_spec_preset,
        "is_backend_only": is_backend_only,
    }


def determine_skill_category(skill_data: dict[str, Any]) -> str:
    """Determine fine-grained category: UI / Frontend, Backend / API, Audit & Security, Architecture & Design, Testing & QA, Documentation & Rules, DevOps & Workflow."""
    fm = skill_data.get("frontmatter", {})
    if fm.get("category"):
        return str(fm["category"]).strip()

    name = (skill_data.get("name") or "").lower()
    title = (skill_data.get("title") or "").lower()
    description = (skill_data.get("description") or "").lower()
    combined = f"{name} {title} {description}"

    if any(kw in combined for kw in ("ui", "ux", "frontend", "react", "css", "tailwind", "web-app", "mobile-app", "gaps", "visual", "chart")):
        return "UI / Frontend"
    if any(kw in combined for kw in ("backend", "api", "django", "python", "database", "sql", "server", "endpoint", "query")):
        return "Backend / API"
    if any(kw in combined for kw in ("security", "audit", "slop", "vulnerability", "hygiene", "autolint", "forensic", "inspection")):
        return "Audit & Security"
    if any(kw in combined for kw in ("architecture", "domain-modeling", "design", "boundary", "module", "structure", "codebase-design")):
        return "Architecture & Design"
    if any(kw in combined for kw in ("test", "testing", "tdd", "validation", "qa", "benchmark")):
        return "Testing & QA"
    if any(kw in combined for kw in ("doc", "documentation", "spec", "directive", "rule", "guideline", "writing", "roadmap")):
        return "Documentation & Rules"
    if any(kw in combined for kw in ("git", "guardrail", "handoff", "workflow", "subagent", "triage", "ci", "cd")):
        return "DevOps & Workflow"

    return "General Engineering"


KNOWN_PYTHON_PACKAGES = {
    "pypdf": "pypdf",
    "pdfplumber": "pdfplumber",
    "pymupdf": "PyMuPDF",
    "fitz": "PyMuPDF",
    "reportlab": "reportlab",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "openpyxl": "openpyxl",
    "pandas": "pandas",
    "matplotlib": "matplotlib",
    "seaborn": "seaborn",
    "pil": "Pillow",
    "pillow": "Pillow",
    "yaml": "PyYAML",
    "pyyaml": "PyYAML",
    "cv2": "opencv-python",
    "bs4": "beautifulsoup4",
    "beautifulsoup4": "beautifulsoup4",
    "requests": "requests",
    "httpx": "httpx",
    "scipy": "scipy",
    "numpy": "numpy",
    "playwright": "playwright",
    "selenium": "selenium",
    "pytesseract": "pytesseract",
    "pdf2image": "pdf2image",
}

KNOWN_SYSTEM_TOOLS = {
    "tesseract": "tesseract",
    "pdftotext": "pdftotext",
    "pdfimages": "pdfimages",
    "ffmpeg": "ffmpeg",
    "graphviz": "graphviz",
    "dot": "graphviz",
    "pandoc": "pandoc",
    "git-lfs": "git-lfs",
    "imagemagick": "convert",
    "convert": "convert",
    "poppler": "pdftotext",
}


def _extract_python_ast_imports(skill_dir: Path) -> set[str]:
    """Parse all .py files in skill_dir using AST and extract third-party imported module names."""
    imported_mods = set()
    std_libs = getattr(sys, "stdlib_module_names", set())

    for py_file in skill_dir.glob("**/*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="ignore"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        mod = alias.name.split(".")[0]
                        if mod and mod not in std_libs and not mod.startswith("_"):
                            imported_mods.add(mod)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        mod = node.module.split(".")[0]
                        if mod and mod not in std_libs and not mod.startswith("_"):
                            imported_mods.add(mod)
        except Exception:
            pass

    local_subdirs = {d.name.lower() for d in skill_dir.glob("**/*") if d.is_dir()}
    local_files = {f.stem.lower() for f in skill_dir.glob("**/*.py")}
    local_names = local_subdirs.union(local_files)

    third_party = set()
    for m in imported_mods:
        if m.lower() not in local_names:
            third_party.add(m)

    return third_party


def detect_skill_dependencies(skill_dir: Path) -> dict[str, Any]:
    """
    Recursively scan skill directory and parent files for manifests (requirements.txt, package.json, pyproject.toml),
    doc files (SKILL.md, README.md, INSTALL.md, REFERENCE.md), and Python AST imports to extract dependencies.
    """
    python_deps: set[str] = set()
    npm_deps: set[str] = set()
    system_tools: set[str] = set()
    scanned_files: set[str] = set()

    if not skill_dir.is_dir():
        return {"python": [], "npm": [], "system_all": [], "missing_system": [], "scanned_files": []}

    # 1. Manifest file detection
    search_dirs = [skill_dir]
    if skill_dir.parent != skill_dir and ((skill_dir.parent / ".claude").exists() or (skill_dir.parent / "requirements.txt").exists()):
        search_dirs.append(skill_dir.parent)

    for s_dir in search_dirs:
        req_file = s_dir / "requirements.txt"
        if req_file.is_file():
            scanned_files.add(req_file.name)
            for line in req_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    pkg = re.split(r"[=<>;~]", line)[0].strip()
                    if pkg and not pkg.startswith("-"):
                        python_deps.add(pkg)

        pkg_json = s_dir / "package.json"
        if pkg_json.is_file():
            scanned_files.add(pkg_json.name)
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8", errors="ignore"))
                for dep_key in ("dependencies", "devDependencies"):
                    for k in data.get(dep_key, {}).keys():
                        npm_deps.add(k)
            except Exception:
                pass

    # 2. AST Code Analysis for Python imports
    ast_imports = _extract_python_ast_imports(skill_dir)
    for mod in ast_imports:
        target_pkg = KNOWN_PYTHON_PACKAGES.get(mod.lower(), mod)
        python_deps.add(target_pkg)

    # 3. Documentation file scanning
    files_to_scan = (
        list(skill_dir.glob("*.md"))
        + list(skill_dir.glob("*.txt"))
        + list(skill_dir.glob("*.sh"))
    )

    for sf in files_to_scan:
        scanned_files.add(sf.name)
        content = sf.read_text(encoding="utf-8", errors="ignore")

        for m in re.finditer(r"pip(?:3)?\s+install\s+([^\n#;`]+)", content, re.IGNORECASE):
            line = m.group(1).strip()
            for t in line.split():
                t = t.strip("`'\"(),")
                if t and not t.startswith("-") and t.lower() not in ("import", "from", "and", "or", "to", "in", "for", "with", "the", "a", "an"):
                    target_pkg = KNOWN_PYTHON_PACKAGES.get(t.lower(), t)
                    python_deps.add(target_pkg)

        for m in re.finditer(r"npm\s+install\s+([^\n#;`]+)", content, re.IGNORECASE):
            line = m.group(1).strip()
            for t in line.split():
                t = t.strip("`'\"(),")
                if t and not t.startswith("-") and t.lower() not in ("--save", "--save-dev", "-g", "-d"):
                    npm_deps.add(t)

        for m in re.finditer(r"(?:brew|apt|apt-get)\s+install\s+([^\n#;`]+)", content, re.IGNORECASE):
            line = m.group(1).strip()
            for t in line.split():
                t = t.strip("`'\"(),")
                if t and not t.startswith("-") and t.lower() not in ("update", "upgrade", "-y"):
                    system_tools.add(t)

        content_lower = content.lower()
        for mod, pkg in KNOWN_PYTHON_PACKAGES.items():
            if f"import {mod}" in content_lower or f"from {mod}" in content_lower or f"require('{mod}')" in content_lower or f'require("{mod}")' in content_lower:
                python_deps.add(pkg)

        for tool, binary in KNOWN_SYSTEM_TOOLS.items():
            if tool in content_lower:
                system_tools.add(binary)

    missing_sys = []
    for tool in sorted(system_tools):
        if not shutil.which(tool):
            missing_sys.append(tool)

    return {
        "python": sorted(list(python_deps)),
        "npm": sorted(list(npm_deps)),
        "system_all": sorted(list(system_tools)),
        "missing_system": missing_sys,
        "scanned_files": sorted(list(scanned_files)),
    }


def install_skill_dependencies(skill_dir_name: str, skills_dir: Path | None = None) -> dict[str, Any]:
    """Universal Dynamic Installer: Pre-installs detected Python PyPI & npm dependencies and runs setup scripts."""
    target_skills_dir = skills_dir or get_skills_dir()
    clean_name = skill_dir_name.strip().lower()

    target_dir = target_skills_dir / clean_name
    if not target_dir.is_dir():
        for item in target_skills_dir.iterdir():
            if item.is_dir() and item.name.lower() == clean_name:
                target_dir = item
                break

    if not target_dir.is_dir():
        raise ValueError(f"Skill '{skill_dir_name}' not found under .claude/skills/")

    # 1. Run setup scripts if present
    setup_sh = target_dir / "setup.sh"
    if setup_sh.is_file():
        try:
            subprocess.run(["bash", str(setup_sh)], cwd=target_dir, timeout=60)
        except Exception as exc:
            logger.warning("Failed executing setup.sh for %s: %s", target_dir.name, exc)

    deps = detect_skill_dependencies(target_dir)
    python_pkgs = deps.get("python", [])
    npm_pkgs = deps.get("npm", [])

    preset_file = Path(__file__).resolve()
    root_dir = preset_file.parents[3]
    venv_pip = root_dir / ".venv" / "bin" / "pip"
    if not venv_pip.exists():
        venv_pip = Path(sys.executable).parent / "pip"

    installed_pkgs = []
    failed_pkgs = []

    # 2. Dynamic Python PyPI Installation with Fallbacks
    if python_pkgs and venv_pip.exists():
        for raw_pkg in python_pkgs:
            mapped_pkg = KNOWN_PYTHON_PACKAGES.get(raw_pkg.lower(), raw_pkg)
            success = False

            # First attempt:Mapped or raw package name
            for candidate in (mapped_pkg, f"python-{mapped_pkg}", raw_pkg):
                try:
                    res = subprocess.run(
                        [str(venv_pip), "install", candidate],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=90,
                    )
                    if res.returncode == 0:
                        installed_pkgs.append(candidate)
                        success = True
                        break
                except Exception:
                    pass

            if not success:
                failed_pkgs.append(raw_pkg)

    # 3. Dynamic npm Installation if package.json or npm commands are present
    if npm_pkgs and shutil.which("npm"):
        for n_pkg in npm_pkgs:
            try:
                subprocess.run(
                    ["npm", "install", "--no-save", n_pkg],
                    cwd=root_dir / "taskit" / "taskit-frontend",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=90,
                )
            except Exception as exc:
                logger.warning("Failed installing npm package %s: %s", n_pkg, exc)

    missing_sys = deps.get("missing_system", [])
    status = "ready"
    if failed_pkgs:
        status = "partial_error"
    elif missing_sys:
        status = "missing_system_cli"

    return {
        "status": status,
        "skill_dir": target_dir.name,
        "python_dependencies": python_pkgs,
        "installed_python": installed_pkgs,
        "failed_python": failed_pkgs,
        "missing_system_cli": missing_sys,
        "scanned_files": deps.get("scanned_files", []),
    }


def list_installed_skills(skills_dir: Path | None = None, user_only: bool = False) -> list[dict[str, Any]]:
    """Scan ``.claude/skills/`` and return list of installed skill dictionaries."""
    target_dir = skills_dir or get_skills_dir()
    if not target_dir.is_dir():
        return []

    results = []
    for item in sorted(target_dir.iterdir()):
        if not item.is_dir():
            continue
        skill_md = item / "SKILL.md"
        if not skill_md.is_file():
            continue

        skill_info = parse_skill_md(skill_md)
        fm = skill_info.get("frontmatter", {})

        is_generated = fm.get("generated") is True or bool(fm.get("generated_from"))
        is_imported = fm.get("imported") is True or fm.get("user_imported") is True or bool(fm.get("imported_from")) or not is_generated

        if user_only and (is_generated or not is_imported):
            continue

        classification = classify_skill_relevance(skill_info)
        is_generated = fm.get("generated") is True
        category = determine_skill_category(skill_info)

        imported_from = fm.get("imported_from") or fm.get("source") or fm.get("git_url")
        if not imported_from:
            imported_from = "https://github.com/mattpocock/skills"

        deps_data = detect_skill_dependencies(Path(skill_info.get("skill_dir", item)))
        python_deps = deps_data.get("python", [])
        missing_sys = deps_data.get("missing_system", [])

        dep_status = "ready"
        if missing_sys:
            dep_status = "missing_system_cli"
        elif python_deps:
            missing_py = []
            for pkg in python_deps:
                spec_name = pkg.replace("-", "_").lower()
                try:
                    if importlib.util.find_spec(spec_name) is None:
                        missing_py.append(pkg)
                except Exception:
                    missing_py.append(pkg)
            if missing_py:
                dep_status = "needs_install"

        preset_enabled = fm.get("preset_enabled", False) is True

        results.append({
            "name": skill_info["name"],
            "title": skill_info["title"],
            "description": skill_info["description"],
            "path": skill_info["path"],
            "directory": item.name,
            "category": category,
            "imported_from": str(imported_from),
            "is_generated": is_generated,
            "is_imported": is_imported,
            "preset_enabled": preset_enabled,
            "dependencies_status": dep_status,
            "python_dependencies": python_deps,
            "missing_system_cli": missing_sys,
            "scanned_files": deps_data.get("scanned_files", []),
            **classification,
        })

    return results


def _parse_github_url(url: str) -> tuple[str, str | None]:
    """Parse GitHub URL into (base_repo_url, optional_subpath)."""
    # e.g., https://github.com/mattpocock/skills/tree/main/skills/ts-reset
    # e.g., https://github.com/mattpocock/skills/blob/main/skills/ts-reset/SKILL.md
    tree_match = re.match(r"^(https://github\.com/[^/]+/[^/]+)/(?:tree|blob)/[^/]+/(.*)$", url)
    if tree_match:
        base_repo = tree_match.group(1) + ".git"
        subpath = tree_match.group(2)
        if subpath.endswith("/SKILL.md") or subpath == "SKILL.md":
            subpath = os.path.dirname(subpath)
        return base_repo, subpath or None
    return url, None


def derive_source_prefix(source: str, filename: str | None = None) -> str:
    """
    Derive a clean, concise prefix for skills based on the import source.
    Examples:
      - 'https://github.com/mattpocock/skills' -> 'matt-'
      - 'https://github.com/anthropics/anthropic-quickstarts' -> 'anthropic-'
      - 'https://github.com/vercel/ai' -> 'vercel-'
      - Single SKILL.md file or raw markdown url -> 'md-'
      - Uploaded archive 'awesome-skills.zip' -> 'awesome-' or 'zip-' (if generic -> 'zip-')
      - Local folder '/path/to/my-skills' -> 'my-skills-' or 'local-'
    """
    if not source:
        return "imp-"

    clean_source = source.strip()

    # 1. Direct single markdown file upload or raw SKILL.md URL
    if filename and filename.lower().endswith((".md", ".markdown")):
        return "md-"
    if clean_source.lower().endswith(("/skill.md", ".md", ".markdown")):
        return "md-"

    # 2. File upload label e.g. "File Upload: foo.zip" or "File Upload: SKILL.md"
    if clean_source.startswith("File Upload:"):
        file_part = clean_source.replace("File Upload:", "").strip()
        file_lower = file_part.lower()
        if file_lower.endswith((".md", ".markdown")):
            return "md-"
        if file_lower.endswith((".zip", ".tar.gz", ".tgz")):
            stem = Path(file_part).stem
            if stem.lower().endswith(".tar"):
                stem = Path(stem).stem
            clean_stem = re.sub(r"[^a-zA-Z0-9_-]", "-", stem).strip("-").lower()
            clean_stem = re.sub(r"-(skills|skill|archive|repo)$", "", clean_stem)
            if clean_stem in ("", "skills", "skill", "archive", "pkg", "extracted", "file"):
                return "zip-"
            short_stem = clean_stem[:10].rstrip("-")
            return f"{short_stem}-"
        return "up-"

    # 3. GitHub or Git URLs
    gh_match = re.search(r"github\.com/([^/]+)/([^/\.]+)", clean_source, re.IGNORECASE)
    if gh_match:
        owner = gh_match.group(1).lower()
        repo = gh_match.group(2).lower()

        if "mattpocock" in owner:
            return "matt-"
        if "anthropics" in owner or "anthropic" in owner:
            return "anthropic-"
        if "openai" in owner:
            return "openai-"
        if "google" in owner:
            return "google-"

        clean_owner = re.sub(r"[^a-zA-Z0-9_-]", "-", owner).strip("-")
        if clean_owner:
            short_owner = clean_owner[:10].rstrip("-")
            return f"{short_owner}-"

        clean_repo = re.sub(r"[^a-zA-Z0-9_-]", "-", repo).strip("-")
        clean_repo = re.sub(r"-(skills|skill|repo)$", "", clean_repo)
        if clean_repo:
            return f"{clean_repo[:10].rstrip('-')}-"
        return "gh-"

    # 4. Generic URLs
    if clean_source.startswith(("http://", "https://")):
        if clean_source.lower().endswith(".zip"):
            return "zip-"
        return "web-"

    # 5. Local path or archive
    local_p = Path(clean_source)
    if local_p.suffix.lower() in (".md", ".markdown"):
        return "md-"
    if local_p.suffix.lower() == ".zip":
        stem = local_p.stem.lower()
        if "mattpocock" in stem:
            return "matt-"
        clean_stem = re.sub(r"[-_](skills|skill|archive|repo)$", "", stem)
        clean_stem = re.sub(r"[^a-zA-Z0-9_-]", "-", clean_stem).strip("-")
        if clean_stem and clean_stem not in ("skills", "skill", "archive", "pkg"):
            return f"{clean_stem[:10].rstrip('-')}-"
        return "zip-"
    if local_p.is_dir():
        folder_name = local_p.name.lower()
        if "mattpocock" in folder_name:
            return "matt-"
        clean_folder = re.sub(r"[-_](skills|skill|archive|repo|source)$", "", folder_name)
        clean_folder = re.sub(r"[^a-zA-Z0-9_-]", "-", clean_folder).strip("-")
        if clean_folder and clean_folder not in ("skills", "skill", "claude", "tmp", "temp"):
            return f"{clean_folder[:10].rstrip('-')}-"
        return "local-"

    return "imp-"


def apply_skill_prefix(raw_name: str, prefix: str) -> str:
    """
    Apply prefix to a skill name, stripping any preexisting 'hk-' prefix and
    preventing duplicate prefixing.
    Examples:
      - raw_name: 'ts-reset', prefix: 'matt-' -> 'matt-ts-reset'
      - raw_name: 'hk-ts-reset', prefix: 'matt-' -> 'matt-ts-reset'
      - raw_name: 'matt-ts-reset', prefix: 'matt-' -> 'matt-ts-reset'
      - raw_name: 'md-sample', prefix: 'md-' -> 'md-sample'
    """
    clean = sanitize_skill_name(raw_name).strip().lower()

    if clean.startswith("hk-"):
        clean = clean[3:]

    clean_prefix = prefix.strip().lower()
    if not clean_prefix.endswith("-"):
        clean_prefix = f"{clean_prefix}-"

    if clean.startswith(clean_prefix):
        return clean

    return f"{clean_prefix}{clean}"


def _is_installed_skill_dir(name: str, installed_dirs: set[str]) -> bool:
    clean = (name or "").strip().lower()
    clean_no_hk = clean.replace("hk-", "")
    target_hk = f"hk-{clean_no_hk}"
    return (clean in installed_dirs) or (clean_no_hk in installed_dirs) or (target_hk in installed_dirs)


def ensure_imported_frontmatter(dest_skill_md: Path, source: str, final_name: str) -> None:
    """Ensure dest_skill_md has valid frontmatter with imported: true, imported_from, and name set to final_name."""
    if not dest_skill_md.is_file():
        return
    try:
        text = dest_skill_md.read_text(encoding="utf-8")
        m = _FRONTMATTER_RE.match(text)
        if m:
            fm_text, body = m.groups()
            fm_dict = yaml.safe_load(fm_text) or {}
        else:
            fm_dict = {}
            body = text
        fm_dict["imported"] = True
        fm_dict["imported_from"] = source
        fm_dict["name"] = final_name
        new_fm = yaml.safe_dump(fm_dict, sort_keys=False, allow_unicode=True)
        dest_skill_md.write_text(f"---\n{new_fm}---\n\n{body.strip()}\n", encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed writing imported frontmatter to %s: %s", dest_skill_md, exc)


def get_installed_skill_identifiers(target_dir: Path) -> set[str]:
    """Get all installed skill names and directory names as lowercased set directly from disk."""
    if not target_dir.is_dir():
        return set()
    
    names = set()
    for item in target_dir.iterdir():
        if not item.is_dir() or item.name.startswith("."):
            continue
        
        folder_name = item.name.lower()
        names.add(folder_name)
        names.add(folder_name.replace("hk-", ""))

        skill_md = item / "SKILL.md"
        if skill_md.is_file():
            try:
                skill_info = parse_skill_md(skill_md)
                if skill_info.get("name"):
                    raw_n = skill_info["name"].lower()
                    names.add(raw_n)
                    names.add(raw_n.replace("hk-", ""))
            except Exception:
                pass

    return names


def save_rejected_skills(rejected_list: list[dict[str, Any]], target_dir: Path | None = None) -> None:
    """Save skipped / rejected skill metadata to .claude/skills/.rejected_skills.json for persistence."""
    t_dir = target_dir or get_skills_dir()
    try:
        t_dir.mkdir(parents=True, exist_ok=True)
        store_path = t_dir / ".rejected_skills.json"
        
        existing = load_rejected_skills(t_dir)
        existing_map = {r["name"]: r for r in existing if isinstance(r, dict) and r.get("name")}
        for item in rejected_list:
            if isinstance(item, dict) and item.get("name"):
                existing_map[item["name"]] = item
                
        installed_names = get_installed_skill_identifiers(t_dir)
        final_list = [v for k, v in existing_map.items() if not _is_installed_skill_dir(k, installed_names)]
        
        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(final_list, f, indent=2)
    except Exception as exc:
        logger.warning("Failed saving rejected skills cache: %s", exc)


def load_rejected_skills(target_dir: Path | None = None) -> list[dict[str, Any]]:
    """Load skipped / rejected skills from .claude/skills/.rejected_skills.json, excluding installed skills and sorted descending by score."""
    t_dir = target_dir or get_skills_dir()
    store_path = t_dir / ".rejected_skills.json"
    if not store_path.exists():
        return []
    try:
        with open(store_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            installed_names = get_installed_skill_identifiers(t_dir)
            filtered = [v for v in data if isinstance(v, dict) and v.get("name") and not _is_installed_skill_dir(v["name"], installed_names)]
            filtered.sort(key=lambda item: int(item.get("usefulness_score", 0)), reverse=True)
            return filtered
    except Exception as exc:
        logger.warning("Error loading rejected skills: %s", exc)
    return []


def score_skill_usefulness(skill_data: dict[str, Any]) -> tuple[bool, int, dict[str, Any]]:
    """
    Score a skill's usefulness for Harness Kit development using Approach 3:
    Hybrid multi-factor scoring (regex word boundaries, structural completeness,
    non-engineering anti-pattern filtering, weighted technical domain topics).

    Returns (is_useful: bool, composite_score: int, details: dict).
    """
    fm = skill_data.get("frontmatter", {})
    if fm.get("useful") is False or fm.get("skip_import") is True:
        return False, 0, {"reason": "Explicit frontmatter override (useful: false / skip_import: true)"}
    if fm.get("useful") is True:
        return True, 100, {"reason": "Explicit frontmatter override (useful: true)"}

    name = (skill_data.get("name") or "").strip().lower()
    title = (skill_data.get("title") or "").strip().lower()
    description = (skill_data.get("description") or "").strip().lower()
    body = (skill_data.get("body") or "").strip().lower()
    combined = f"{name} {title} {description} {body[:1000]}"

    # 1. Blacklist & Anti-Pattern Detection (Word Boundaries)
    blacklist_pattern = r"\b(ask-matt|wait-what|writing-beats|writing-fragments|writing-shape|scaffold-exercises|handoff|breadcrumbs)\b"
    if re.search(blacklist_pattern, name) or re.search(blacklist_pattern, title):
        return False, 0, {"reason": "Blacklisted scratch / internal interactive prompt name"}

    non_eng_antipattern = r"\b(recipe|recipes|cooking|ingredients|novel|fiction|storytelling|fitness|workout|diet|poetry|jokes)\b"
    if re.search(non_eng_antipattern, name) or re.search(non_eng_antipattern, title) or re.search(non_eng_antipattern, description):
        return False, 0, {"reason": "Non-engineering domain anti-pattern match"}

    # 2. High-Confidence Technical Engineering Terms (+15 pts each, max 45)
    high_tech_terms = [
        r"\barchitecture\b", r"\brefactor(ing)?\b", r"\bsecurity\b", r"\baudit(s)?\b",
        r"\bslop\b", r"\bperformance\b", r"\btesting\b", r"\btest-driven\b", r"\btdd\b",
        r"\btypescript\b", r"\bts\b", r"\bjavascript\b", r"\bjs\b", r"\bpython\b",
        r"\bdjango\b", r"\breact\b", r"\bfrontend\b", r"\bbackend\b", r"\bci-cd\b",
        r"\bobservability\b", r"\bdebugging\b", r"\blint(er|ing)?\b", r"\bautolint\b",
        r"\bcode-review(s)?\b", r"\bcodebase(s)?\b"
    ]
    high_score = 0
    high_matches = []
    for term in high_tech_terms:
        if re.search(term, combined):
            high_score += 15
            high_matches.append(term.replace(r"\b", "").replace(r"(ing)?", "").replace(r"(er|ing)?", "").replace(r"(s)?", ""))
            if high_score >= 45:
                break

    # 3. Medium-Confidence Tooling & Directive Terms (+8 pts each, max 24)
    med_tech_terms = [
        r"\brule(s)?\b", r"\bstandard(s)?\b", r"\bdiscipline(s)?\b", r"\bdirective(s)?\b",
        r"\bguideline(s)?\b", r"\bguardrail(s)?\b", r"\bboundary\b", r"\bboundaries\b",
        r"\bvalidation(s)?\b", r"\bhygiene\b", r"\bquality\b", r"\bpattern(s)?\b",
        r"\bpractice(s)?\b", r"\bapi(s)?\b", r"\bui\b", r"\bux\b", r"\bcss\b",
        r"\bhtml\b", r"\bgit\b", r"\bmigration(s)?\b", r"\badrs?\b", r"\bclean\b", r"\bspec(s)?\b",
        r"\bpdf(kit)?\b", r"\bgenerator(s)?\b", r"\bexporter(s)?\b", r"\btool(s|ing)?\b", r"\bbuild\b"
    ]
    med_score = 0
    med_matches = []
    for term in med_tech_terms:
        if re.search(term, combined):
            med_score += 8
            med_matches.append(term.replace(r"\b", "").replace(r"(s)?", ""))
            if med_score >= 24:
                break

    # 4. Structural Completeness Score (max 28 pts)
    structural_score = 0
    struct_details = []

    if name and description:
        structural_score += 5
        struct_details.append("has_metadata")

    body_len = len(body)
    if body_len >= 200:
        structural_score += 10
        struct_details.append("body_length_>=200")
    elif body_len >= 80:
        structural_score += 5
        struct_details.append("body_length_>=80")

    # Header check (Markdown headers)
    if re.search(r"^#{1,4}\s+(process|trigger|guideline|verification|overview|quality|rules|steps|usage)", body, re.MULTILINE | re.IGNORECASE):
        structural_score += 8
        struct_details.append("has_standard_headers")

    if "```" in body:
        structural_score += 5
        struct_details.append("has_code_blocks")

    # 5. Preset Classification Bonus (+20 pts)
    classification = classify_skill_relevance(skill_data)
    preset_bonus = 0
    if classification["is_task_preset"] or classification["is_spec_preset"]:
        preset_bonus = 20

    total_score = min(100, high_score + med_score + structural_score + preset_bonus)
    THRESHOLD = 25
    is_useful = total_score >= THRESHOLD

    details = {
        "score": total_score,
        "high_score": high_score,
        "high_matches": high_matches,
        "med_score": med_score,
        "med_matches": med_matches,
        "structural_score": structural_score,
        "struct_details": struct_details,
        "preset_bonus": preset_bonus,
        "threshold": THRESHOLD,
    }

    return is_useful, total_score, details


def is_useful_skill(skill_data: dict[str, Any]) -> bool:
    """Determine if a skill is useful for Harness Kit development and should be imported."""
    is_useful, _, _ = score_skill_usefulness(skill_data)
    return is_useful



def find_existing_skill_dir(skill_info: dict[str, Any], skills_dir: Path, target_prefixed_name: str | None = None) -> Path | None:
    """Find existing skill directory under skills_dir matching target_prefixed_name, raw name, f'hk-{name}', or SKILL.md name."""
    raw_name = (skill_info.get("name") or "").strip().lower()
    names_to_check = set()
    if target_prefixed_name:
        names_to_check.add(target_prefixed_name.strip().lower())
    if raw_name:
        names_to_check.add(raw_name)
        raw_no_hk = raw_name.replace("hk-", "")
        names_to_check.add(raw_no_hk)
        names_to_check.add(f"hk-{raw_no_hk}")

    for check_name in list(names_to_check):
        if not check_name:
            continue
        if (skills_dir / check_name).is_dir():
            return skills_dir / check_name

    for item in skills_dir.iterdir():
        if not item.is_dir():
            continue
        item_name = item.name.lower()
        if item_name in names_to_check:
            return item

        skill_md = item / "SKILL.md"
        if skill_md.is_file():
            try:
                fm_info = parse_skill_md(skill_md)
                fm_name = (fm_info.get("name") or "").strip().lower()
                if fm_name in names_to_check or fm_name.replace("hk-", "") in names_to_check:
                    return item
            except Exception:
                pass

    return None


def import_skills_from_source(source: str, force: bool = False, target_skill_name: str | None = None, skills_dir: Path | None = None) -> dict[str, Any]:
    """
    Import skills from a Git repository URL (including GitHub subpath URLs),
    local folder path, raw SKILL.md URL, or archive URL into .claude/skills/.
    """
    if not source or not source.strip():
        raise ValueError("Source URL or path is required")

    source = source.strip()
    target_skills_dir = skills_dir or get_skills_dir()
    target_skills_dir.mkdir(parents=True, exist_ok=True)

    imported: list[str] = []
    skipped: list[dict[str, str]] = []  # returned in response but not all persisted
    score_rejected: list[dict[str, Any]] = []  # only low-score rejections, persisted to cache
    messages: list[str] = []

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)

        if source.startswith("http://") or source.startswith("https://"):
            git_url, subpath = _parse_github_url(source)
            if git_url:
                _clone_git_repo(git_url, tmp_dir)
                search_root = (tmp_dir / "repo" / subpath) if subpath else (tmp_dir / "repo")
            elif source.endswith(".zip"):
                _download_and_extract_zip(source, tmp_dir)
                search_root = tmp_dir / "extracted"
            else:
                _fetch_raw_skill(source, tmp_dir)
                search_root = tmp_dir
        else:
            local_path = Path(source).expanduser().resolve()
            if not local_path.exists():
                raise ValueError(f"Local path '{source}' does not exist")
            if local_path.is_file():
                if local_path.name.endswith(".zip"):
                    with zipfile.ZipFile(local_path, "r") as z:
                        safe_extract_zip(z, tmp_dir)
                else:
                    dest_skill = tmp_dir / "custom-skill"
                    dest_skill.mkdir(parents=True, exist_ok=True)
                    shutil.copy(local_path, dest_skill / "SKILL.md")
                search_root = tmp_dir
            else:
                search_root = local_path

        if not search_root.exists():
            raise ValueError(f"Target directory '{search_root}' does not exist in source '{source}'")

        skill_files = list(search_root.glob("**/SKILL.md"))
        if not skill_files:
            raise ValueError(f"No SKILL.md files found in '{source}'")

        source_prefix = derive_source_prefix(source)

        if target_skill_name:
            clean_t = target_skill_name.strip().lower().replace("hk-", "")
            filtered = []
            for f in skill_files:
                parsed_fm = parse_skill_md(f)
                r_name = sanitize_skill_name(parsed_fm.get("name") or f.parent.name)
                f_name = apply_skill_prefix(r_name, source_prefix)
                if (
                    clean_t == f_name.lower().replace("hk-", "")
                    or clean_t == r_name.lower().replace("hk-", "")
                    or clean_t == f.parent.name.lower().replace("hk-", "")
                ):
                    filtered.append(f)
            if filtered:
                skill_files = filtered

        for skill_file in skill_files:
            skill_info = parse_skill_md(skill_file)
            raw_name = sanitize_skill_name(skill_info.get("name") or skill_file.parent.name)
            final_name = apply_skill_prefix(raw_name, source_prefix)

            # Filter out non-useful skills for Harness Kit (unless force=True)
            is_useful, usefulness_score, score_details = score_skill_usefulness(skill_info)
            if not is_useful and not force:
                msg = f"Skill '{final_name}' skipped (usefulness score {usefulness_score}/100 below threshold)."
                entry = {
                    "name": final_name,
                    "title": skill_info.get("title") or raw_name,
                    "description": skill_info.get("description", ""),
                    "directory": final_name,
                    "source": source,
                    "category": skill_info.get("category") or "General Skill",
                    "reason": msg,
                    "usefulness_score": usefulness_score,
                }
                skipped.append(entry)
                # Only persist to rejected cache if NOT already installed on disk
                installed_ids = get_installed_skill_identifiers(target_skills_dir)
                if not _is_installed_skill_dir(final_name, installed_ids):
                    score_rejected.append(entry)
                continue

            existing_dir = find_existing_skill_dir(skill_info, target_skills_dir, target_prefixed_name=final_name)
            if existing_dir and not force:
                # Already installed — add to skipped response but NOT to persistent rejected cache
                msg = f"Skill '{final_name}' already exists in .claude/skills/ as '{existing_dir.name}'."
                skipped.append({
                    "name": final_name,
                    "title": skill_info.get("title") or raw_name,
                    "description": skill_info.get("description", ""),
                    "directory": existing_dir.name,
                    "source": source,
                    "category": skill_info.get("category") or "General Skill",
                    "reason": msg,
                    "usefulness_score": usefulness_score,
                })
                messages.append(msg)
                continue

            dest_dir = existing_dir if existing_dir else (target_skills_dir / final_name)
            if not dest_dir.resolve().is_relative_to(target_skills_dir.resolve()):
                raise ValueError(f"Security Exception: Skill name '{final_name}' attempts path traversal outside destination.")

            # Copy skill directory to .claude/skills/<final_name>
            src_dir = skill_file.parent

            if dest_dir.exists():
                shutil.rmtree(dest_dir)

            shutil.copytree(src_dir, dest_dir)

            # Mark frontmatter with imported: true, imported_from source metadata, and name: final_name
            dest_skill_md = dest_dir / "SKILL.md"
            ensure_imported_frontmatter(dest_skill_md, source, final_name)

            imported.append(final_name)
            messages.append(f"Successfully imported skill '{final_name}'.")

            try:
                install_skill_dependencies(dest_dir.name, target_skills_dir)
            except Exception as exc:
                logger.debug("Silent dependency pre-installation for %s: %s", dest_dir.name, exc)

    save_rejected_skills(score_rejected, target_skills_dir)

    installed = list_installed_skills(target_skills_dir, user_only=True)
    rejected_list = load_rejected_skills(target_skills_dir)
    return {
        "status": "success",
        "imported": imported,
        "skipped": skipped,
        "messages": messages,
        "skills": installed,
        "rejected_skills": rejected_list,
    }


def import_skills_from_upload(file_obj, filename: str, force: bool = False, skills_dir: Path | None = None) -> dict[str, Any]:
    """Import skills from an uploaded file (.zip, .tar.gz, or SKILL.md)."""
    if not filename:
        raise ValueError("Filename is required for upload")

    # Check file size limit (15 MB)
    if hasattr(file_obj, "size") and file_obj.size and file_obj.size > MAX_IMPORT_FILE_SIZE_BYTES:
        raise ValueError(f"File '{filename}' exceeds maximum allowed upload size of 15 MB.")

    target_skills_dir = skills_dir or get_skills_dir()
    target_skills_dir.mkdir(parents=True, exist_ok=True)

    imported_source_label = f"File Upload: {filename}"
    source_prefix = derive_source_prefix(imported_source_label, filename=filename)

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        upload_path = tmp_dir / filename

        written_bytes = 0
        with open(upload_path, "wb") as out:
            for chunk in file_obj.chunks():
                written_bytes += len(chunk)
                if written_bytes > MAX_IMPORT_FILE_SIZE_BYTES:
                    raise ValueError(f"File '{filename}' exceeds maximum allowed upload size of 15 MB.")
                out.write(chunk)

        if filename.lower().endswith(".zip"):
            with zipfile.ZipFile(upload_path, "r") as z:
                safe_extract_zip(z, tmp_dir / "extracted")
            search_root = tmp_dir / "extracted"
        elif filename.lower().endswith((".tar.gz", ".tgz")):
            import tarfile
            with tarfile.open(upload_path, "r:*") as tar:
                safe_extract_tar(tar, tmp_dir / "extracted")
            search_root = tmp_dir / "extracted"
        else:
            clean_stem = sanitize_skill_name(Path(filename).stem)
            if clean_stem.lower() in ("skill", "file"):
                clean_stem = "uploaded-skill"
            dest_skill = tmp_dir / clean_stem
            dest_skill.mkdir(parents=True, exist_ok=True)
            (dest_skill / "SKILL.md").write_bytes(upload_path.read_bytes())
            search_root = dest_skill

        skill_files = list(search_root.glob("**/SKILL.md"))
        if not skill_files:
            raise ValueError(f"No SKILL.md files found in uploaded file '{filename}'")

        imported: list[str] = []
        skipped: list[dict[str, str]] = []
        messages: list[str] = []

        for skill_file in skill_files:
            skill_info = parse_skill_md(skill_file)
            raw_name = sanitize_skill_name(skill_info.get("name") or skill_file.parent.name)
            if raw_name in ("uploaded-skill", "skill", "file") and filename:
                raw_name = sanitize_skill_name(Path(filename).stem)
            final_name = apply_skill_prefix(raw_name, source_prefix)

            # Filter out non-useful skills for Harness Kit (unless force=True or direct single skill file upload)
            is_single_skill_upload = len(skill_files) == 1
            is_useful, usefulness_score, score_details = score_skill_usefulness(skill_info)
            if not is_useful and not force and not is_single_skill_upload:
                msg = f"Skill '{final_name}' skipped (usefulness score {usefulness_score}/100 below threshold)."
                skipped.append({
                    "name": final_name,
                    "title": skill_info.get("title") or raw_name,
                    "description": skill_info.get("description", ""),
                    "directory": final_name,
                    "source": imported_source_label,
                    "category": skill_info.get("category") or "General Skill",
                    "reason": msg,
                    "usefulness_score": usefulness_score,
                })
                continue

            existing_dir = find_existing_skill_dir(skill_info, target_skills_dir, target_prefixed_name=final_name)
            if existing_dir and not force:
                msg = f"Skill '{final_name}' already exists in .claude/skills/ as '{existing_dir.name}'."
                skipped.append({
                    "name": final_name,
                    "title": skill_info.get("title") or raw_name,
                    "description": skill_info.get("description", ""),
                    "directory": existing_dir.name,
                    "source": imported_source_label,
                    "category": skill_info.get("category") or "General Skill",
                    "reason": msg,
                    "usefulness_score": usefulness_score,
                })
                messages.append(msg)
                continue

            dest_dir = existing_dir if existing_dir else (target_skills_dir / final_name)
            if not dest_dir.resolve().is_relative_to(target_skills_dir.resolve()):
                raise ValueError(f"Security Exception: Skill name '{final_name}' attempts path traversal outside destination.")

            src_dir = skill_file.parent
            if dest_dir.exists():
                shutil.rmtree(dest_dir)

            shutil.copytree(src_dir, dest_dir)

            dest_skill_md = dest_dir / "SKILL.md"
            ensure_imported_frontmatter(dest_skill_md, imported_source_label, final_name)

            imported.append(final_name)
            messages.append(f"Successfully imported skill '{final_name}'.")

            try:
                install_skill_dependencies(dest_dir.name, target_skills_dir)
            except Exception as exc:
                logger.debug("Silent dependency pre-installation for %s: %s", dest_dir.name, exc)

    save_rejected_skills(skipped, target_skills_dir)

    installed = list_installed_skills(target_skills_dir, user_only=True)
    rejected_list = load_rejected_skills(target_skills_dir)
    return {
        "status": "success",
        "imported": imported,
        "skipped": skipped,
        "messages": messages,
        "skills": installed,
        "rejected_skills": rejected_list,
    }


def _clone_git_repo(repo_url: str, target_dir: Path) -> None:
    """Clone git repository with depth 1."""
    import subprocess
    cmd = ["git", "clone", "--depth", "1", repo_url, str(target_dir / "repo")]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise ValueError(f"Invalid URL or Git repository not found: '{repo_url}'. Please enter a valid GitHub repo, raw SKILL.md link, or local path.")


def _fetch_raw_skill(url: str, target_dir: Path) -> None:
    """Fetch raw SKILL.md content from HTTP URL with max 15 MB download cap."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "HarnessKit-SkillImporter/1.0"})
        with urllib.request.urlopen(req) as resp:
            content_bytes = bytearray()
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                content_bytes.extend(chunk)
                if len(content_bytes) > MAX_IMPORT_FILE_SIZE_BYTES:
                    raise ValueError("Downloaded skill file exceeds maximum allowed size of 15 MB.")
            content = content_bytes.decode("utf-8")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Invalid URL or skill file not found: '{url}'. Please enter a valid GitHub repo, raw SKILL.md link, or local path.")

    dest_skill = target_dir / "imported-skill"
    dest_skill.mkdir(parents=True, exist_ok=True)
    (dest_skill / "SKILL.md").write_text(content, encoding="utf-8")


def _download_and_extract_zip(url: str, target_dir: Path) -> None:
    """Download and extract a zip archive from URL with max 15 MB download cap and safe extraction."""
    zip_path = target_dir / "archive.zip"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "HarnessKit-SkillImporter/1.0"})
        with urllib.request.urlopen(req) as resp, open(zip_path, "wb") as out:
            downloaded = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > MAX_IMPORT_FILE_SIZE_BYTES:
                    raise ValueError("Downloaded zip archive exceeds maximum allowed size of 15 MB.")
                out.write(chunk)
        with zipfile.ZipFile(zip_path, "r") as z:
            safe_extract_zip(z, target_dir / "extracted")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Failed downloading zip from '{url}': {exc}")


def delete_installed_skill(skill_name: str, skills_dir: Path | None = None) -> dict[str, Any]:
    """Delete an installed skill directory from disk."""
    target_skills_dir = skills_dir or get_skills_dir()
    clean_target = skill_name.strip().lower()
    clean_target_no_hk = clean_target.replace("hk-", "")

    found_dir = None
    direct_path = target_skills_dir / skill_name
    if direct_path.is_dir():
        found_dir = direct_path
    else:
        for item in target_skills_dir.iterdir():
            if not item.is_dir():
                continue
            item_name_clean = item.name.lower()
            item_name_no_hk = item_name_clean.replace("hk-", "")

            if (
                item_name_clean == clean_target
                or item_name_no_hk == clean_target_no_hk
                or item_name_clean == f"hk-{clean_target}"
                or item_name_clean.endswith(f"-{clean_target}")
                or item_name_clean.endswith(f"-{clean_target_no_hk}")
            ):
                found_dir = item
                break

            skill_md = item / "SKILL.md"
            if skill_md.is_file():
                try:
                    parsed = parse_skill_md(skill_md)
                    fm_name = (parsed.get("name") or "").lower()
                    fm_clean_no_hk = fm_name.replace("hk-", "")
                    if (
                        fm_name == clean_target
                        or fm_clean_no_hk == clean_target_no_hk
                        or fm_name.endswith(f"-{clean_target}")
                        or fm_name.endswith(f"-{clean_target_no_hk}")
                    ):
                        found_dir = item
                        break
                except Exception:
                    pass

    if not found_dir or not found_dir.exists():
        raise ValueError(f"Skill '{skill_name}' not found under .claude/skills/")

    deleted_dir_name = found_dir.name
    shutil.rmtree(found_dir)

    return {
        "status": "success",
        "deleted": deleted_dir_name,
        "skills": list_installed_skills(target_skills_dir, user_only=True),
    }


def delete_all_installed_skills(skills_dir: Path | None = None, user_only: bool = True) -> dict[str, Any]:
    """Delete all installed user/imported skills from .claude/skills/ on disk."""
    target_skills_dir = skills_dir or get_skills_dir()
    deleted_dirs = []
    if target_skills_dir.is_dir():
        installed = list_installed_skills(target_skills_dir, user_only=user_only)
        for s in installed:
            dir_name = s.get("directory")
            if not dir_name:
                continue
            skill_path = target_skills_dir / dir_name
            if skill_path.is_dir():
                try:
                    shutil.rmtree(skill_path)
                    deleted_dirs.append(dir_name)
                except Exception as exc:
                    logger.warning("Failed deleting skill directory %s: %s", skill_path, exc)

    return {
        "status": "success",
        "deleted": deleted_dirs,
        "deleted_count": len(deleted_dirs),
        "skills": list_installed_skills(target_skills_dir, user_only=True),
    }


def clear_rejected_skills(target_dir: Path | None = None) -> dict[str, Any]:
    """Clear all uninstalled / rejected skills from persistence."""
    t_dir = target_dir or get_skills_dir()
    store_path = t_dir / ".rejected_skills.json"
    if store_path.exists():
        try:
            store_path.unlink()
        except Exception as exc:
            logger.warning("Failed removing rejected skills cache %s: %s", store_path, exc)
    return {
        "status": "success",
        "rejected_skills": [],
    }


def toggle_skill_preset(skill_name: str, enabled: bool, skills_dir: Path | None = None) -> dict[str, Any]:
    """Toggle preset_enabled flag in SKILL.md frontmatter for an installed skill."""
    target_skills_dir = skills_dir or get_skills_dir()
    clean_target = skill_name.strip().lower()
    clean_target_no_hk = clean_target.replace("hk-", "")

    found_dir = None
    direct_path = target_skills_dir / skill_name
    if direct_path.is_dir():
        found_dir = direct_path
    else:
        for item in target_skills_dir.iterdir():
            if not item.is_dir():
                continue
            item_name_clean = item.name.lower()
            item_name_no_hk = item_name_clean.replace("hk-", "")

            if (
                item_name_clean == clean_target
                or item_name_no_hk == clean_target_no_hk
                or item_name_clean == f"hk-{clean_target}"
                or item_name_clean.endswith(f"-{clean_target}")
                or item_name_clean.endswith(f"-{clean_target_no_hk}")
            ):
                found_dir = item
                break

            skill_md = item / "SKILL.md"
            if skill_md.is_file():
                try:
                    parsed = parse_skill_md(skill_md)
                    fm_name = (parsed.get("name") or "").lower()
                    fm_clean_no_hk = fm_name.replace("hk-", "")
                    if (
                        fm_name == clean_target
                        or fm_clean_no_hk == clean_target_no_hk
                        or fm_name.endswith(f"-{clean_target}")
                        or fm_name.endswith(f"-{clean_target_no_hk}")
                    ):
                        found_dir = item
                        break
                except Exception:
                    pass

    if not found_dir or not found_dir.exists():
        raise ValueError(f"Skill '{skill_name}' not found under .claude/skills/")

    dest_skill_md = found_dir / "SKILL.md"
    if dest_skill_md.is_file():
        text = dest_skill_md.read_text(encoding="utf-8")
        m = _FRONTMATTER_RE.match(text)
        if m:
            fm_text, body = m.groups()
            fm_dict = yaml.safe_load(fm_text) or {}
        else:
            fm_dict = {}
            body = text
        fm_dict["preset_enabled"] = bool(enabled)
        new_fm = yaml.safe_dump(fm_dict, sort_keys=False, allow_unicode=True)
        dest_skill_md.write_text(f"---\n{new_fm}---\n\n{body.strip()}\n", encoding="utf-8")

    return {
        "status": "success",
        "skill_dir": found_dir.name,
        "preset_enabled": bool(enabled),
        "skills": list_installed_skills(target_skills_dir, user_only=True),
    }


def _clean_skill_desc(desc: str, skill_name: str) -> str:
    cleaned = (desc or "").strip()
    if not cleaned:
        return ""
    cleaned = re.sub(rf"^use\s+skill\s+/{re.escape(skill_name)}\s+to\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(rf"^use\s+skill\s+/{re.escape(skill_name)}\s+", "", cleaned, flags=re.IGNORECASE)
    if cleaned and not cleaned[0].isupper() and len(cleaned) > 1:
        cleaned = cleaned[0].lower() + cleaned[1:]
    return cleaned.rstrip(".")


def get_skills_as_presets(skills_dir: Path | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return custom/imported skills formatted as (task_presets, spec_presets)."""
    skills = list_installed_skills(skills_dir)
    task_presets = []
    spec_presets = []

    for s in skills:
        # ONLY include if explicitly enabled as preset by checking the checkbox!
        if not s.get("preset_enabled"):
            continue

        skill_name = s["name"] if s["name"].startswith("hk-") else f"hk-{s['name']}"
        s_id = skill_name.replace("hk-", "")
        title = s["title"]
        raw_desc = s["description"] or f"Skill /{skill_name}"
        desc_clean = _clean_skill_desc(raw_desc, skill_name)
        detail = f": {desc_clean}" if desc_clean else ""

        # Task Preset: Actionable workflow executed on an existing/built application codebase
        task_presets.append({
            "id": s_id,
            "title": title,
            "description": f"Execute task workflow using skill /{skill_name} on the built application codebase{detail}.",
            "category": "custom-skills",
            "icon": "sparkles",
            "source_skill": skill_name,
            "sort_order": 99,
        })

        # Spec Preset: Directives & guidelines enforced upfront during spec creation & feature development
        spec_presets.append({
            "id": s_id,
            "title": title,
            "description": f"Pre-application spec directive to enforce standards and prevent issues using skill /{skill_name}.",
            "category": "custom-skills",
            "icon": "sparkles",
            "directive": f"Use skill /{skill_name} as a pre-application spec directive to enforce coding standards, maintain architectural hygiene, and prevent anti-patterns upfront while creating feature specs{detail}.",
            "source_skill": skill_name,
            "sort_order": 99,
        })

    return task_presets, spec_presets
