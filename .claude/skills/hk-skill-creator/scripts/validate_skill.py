#!/usr/bin/env python3
"""
Validate a skill's structure, frontmatter, and content quality.

Usage:
    python validate_skill.py <skill-directory>
    python validate_skill.py <skill-directory> --strict

Checks:
  - SKILL.md exists with valid YAML frontmatter
  - Required fields (name, description) present and well-formed
  - name matches directory name
  - No TODO/placeholder text left in description
  - Description has sufficient length and trigger context
  - Description truncation warning (>250 chars)
  - Known frontmatter fields validated
  - context: fork without task instructions detected
  - Referenced resources exist
  - No unreferenced resource files (potential dead code)
  - Body isn't empty or placeholder-only

--strict: treat warnings as errors
"""

import re
import sys
from pathlib import Path


KNOWN_FRONTMATTER_FIELDS = {
    "name", "description", "argument-hint", "allowed-tools",
    "disable-model-invocation", "user-invocable",
    "model", "effort", "context", "agent", "shell",
    "paths", "hooks",
}

VALID_EFFORT_VALUES = {"low", "medium", "high", "max"}
VALID_CONTEXT_VALUES = {"fork"}
VALID_SHELL_VALUES = {"bash", "powershell"}


class ValidationResult:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    @property
    def ok(self):
        return len(self.errors) == 0

    def report(self, strict=False):
        for e in self.errors:
            print(f"  ERROR: {e}")
        for w in self.warnings:
            print(f"  WARN:  {w}")

        total_issues = len(self.errors) + (len(self.warnings) if strict else 0)
        if total_issues == 0:
            print("  Valid!")
        else:
            count = f"{len(self.errors)} error(s)"
            if self.warnings:
                count += f", {len(self.warnings)} warning(s)"
            print(f"  {count}")
        return total_issues == 0


def extract_frontmatter(content):
    """Extract YAML frontmatter from SKILL.md content. Returns (frontmatter_str, body_str) or (None, None)."""
    match = re.match(r'^---\n(.*?)\n---\n?(.*)', content, re.DOTALL)
    if not match:
        return None, None
    return match.group(1), match.group(2)


def parse_frontmatter_field(frontmatter, field):
    """Extract a field value from frontmatter text. Handles quoted and unquoted values."""
    # Try quoted value first (handles multiline)
    match = re.search(rf'^{field}:\s*"((?:[^"\\]|\\.)*)"\s*$', frontmatter, re.MULTILINE)
    if match:
        return match.group(1).replace('\\"', '"')
    # Unquoted single-line
    match = re.search(rf'^{field}:\s*(.+)$', frontmatter, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None


def parse_all_field_names(frontmatter):
    """Extract all field names from frontmatter (ignoring comments)."""
    fields = set()
    for line in frontmatter.split("\n"):
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        match = re.match(r'^([a-z][a-z0-9-]*):', line)
        if match:
            fields.add(match.group(1))
    return fields


def validate_skill(skill_path, strict=False):
    """Validate a skill directory. Returns True if valid."""
    skill_path = Path(skill_path).resolve()
    result = ValidationResult()

    print(f"Validating: {skill_path.name}")

    # --- Structural checks ---

    if not skill_path.is_dir():
        result.error(f"Not a directory: {skill_path}")
        return result.report(strict)

    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        result.error("SKILL.md not found")
        return result.report(strict)

    content = skill_md.read_text()
    if not content.strip():
        result.error("SKILL.md is empty")
        return result.report(strict)

    # --- Frontmatter checks ---

    if not content.startswith("---"):
        result.error("No YAML frontmatter (must start with ---)")
        return result.report(strict)

    frontmatter, body = extract_frontmatter(content)
    if frontmatter is None:
        result.error("Invalid frontmatter format (missing closing ---)")
        return result.report(strict)

    # Check for unknown fields
    used_fields = parse_all_field_names(frontmatter)
    unknown_fields = used_fields - KNOWN_FRONTMATTER_FIELDS
    if unknown_fields:
        result.warn(f"Unknown frontmatter field(s): {', '.join(sorted(unknown_fields))}")

    # Required: name
    name = parse_frontmatter_field(frontmatter, "name")
    if not name:
        result.error("Missing 'name' in frontmatter")
    else:
        # Name format
        if not re.match(r'^[a-z0-9]+(-[a-z0-9]+)*$', name):
            result.error(f"name '{name}' is not valid hyphen-case (lowercase letters, digits, hyphens, no leading/trailing/double hyphens)")
        # Name matches directory
        if name != skill_path.name:
            result.error(f"name '{name}' does not match directory name '{skill_path.name}'")
        # Length
        if len(name) > 64:
            result.error(f"name is {len(name)} chars (max: 64)")
        elif len(name) > 50:
            result.warn(f"name is {len(name)} chars (consider keeping under 50 for readability)")

    # Required: description
    description = parse_frontmatter_field(frontmatter, "description")
    if not description:
        result.error("Missing 'description' in frontmatter")
    else:
        # Check for placeholder text
        if "[TODO" in description or "TODO:" in description:
            result.error("description contains TODO placeholder — must be completed")
        if description.startswith("[") and description.endswith("]"):
            result.error("description looks like an unfilled template placeholder")
        # Length check
        if len(description) < 50:
            result.warn(f"description is only {len(description)} chars — should be detailed enough to trigger reliably (aim for 80+ chars)")
        # Truncation warning
        if len(description) > 250:
            result.warn(f"description is {len(description)} chars — truncated at 250 in skill listing. Front-load the key use case.")
        # Angle brackets (can break YAML/rendering)
        if "<" in description or ">" in description:
            result.warn("description contains angle brackets — may cause YAML parsing issues")
        # Trigger context
        trigger_patterns = ["trigger", "use this", "use when", "whenever", "invoke"]
        has_trigger_context = any(p in description.lower() for p in trigger_patterns)
        if not has_trigger_context:
            result.warn("description doesn't include trigger context (e.g., 'Use when...', 'Triggers on...')")

    # Validate enum fields
    effort = parse_frontmatter_field(frontmatter, "effort")
    if effort and effort not in VALID_EFFORT_VALUES:
        result.error(f"effort '{effort}' is not valid — must be one of: {', '.join(sorted(VALID_EFFORT_VALUES))}")

    context = parse_frontmatter_field(frontmatter, "context")
    if context and context not in VALID_CONTEXT_VALUES:
        result.error(f"context '{context}' is not valid — only 'fork' is supported")

    shell = parse_frontmatter_field(frontmatter, "shell")
    if shell and shell not in VALID_SHELL_VALUES:
        result.error(f"shell '{shell}' is not valid — must be 'bash' or 'powershell'")

    # Validate boolean fields
    for bool_field in ["disable-model-invocation", "user-invocable"]:
        val = parse_frontmatter_field(frontmatter, bool_field)
        if val and val not in ("true", "false"):
            result.error(f"{bool_field} '{val}' is not valid — must be 'true' or 'false'")

    # Check agent without context: fork
    agent = parse_frontmatter_field(frontmatter, "agent")
    if agent and not context:
        result.warn("'agent' field has no effect without 'context: fork'")

    # --- Body checks ---

    if body is not None:
        body_stripped = body.strip()
        if not body_stripped:
            result.error("SKILL.md body is empty (no instructions after frontmatter)")
        else:
            # Check for TODO-only body
            non_todo_lines = [
                line for line in body_stripped.split("\n")
                if line.strip() and "[TODO" not in line and "TODO:" not in line
            ]
            # Discount headers and resource section boilerplate
            substantive_lines = [
                line for line in non_todo_lines
                if not line.strip().startswith("#") and len(line.strip()) > 10
            ]
            if len(substantive_lines) < 3:
                result.warn("SKILL.md body has very little substantive content (mostly TODOs or headers)")

            # Line count
            line_count = len(body_stripped.split("\n"))
            if line_count > 500:
                result.warn(f"SKILL.md body is {line_count} lines (recommended max: 500 — move detail to references/)")

            # Check for context: fork without task instructions
            if context == "fork":
                has_imperative = any(
                    word in body_stripped.lower()
                    for word in ["run ", "find ", "search ", "create ", "analyze ", "generate ", "build ", "check "]
                )
                if not has_imperative:
                    result.warn("context: fork is set but body may lack task instructions — subagent needs explicit actions since it has no conversation context")

    # --- Resource checks ---

    # Collect all resource files
    resource_files = set()
    for subdir in ["scripts", "references", "assets"]:
        subdir_path = skill_path / subdir
        if subdir_path.is_dir():
            for f in subdir_path.rglob("*"):
                if f.is_file():
                    resource_files.add(f.relative_to(skill_path))

    # Check if resources are referenced in SKILL.md
    if resource_files and body:
        unreferenced = []
        for rf in resource_files:
            # Check if filename or path appears in the body
            fname = rf.name
            fpath = str(rf)
            if fname not in body and fpath not in body and str(rf.parent) not in body:
                unreferenced.append(str(rf))
        if unreferenced:
            for uf in unreferenced:
                result.warn(f"Resource '{uf}' is not referenced in SKILL.md")

    # Check for references to non-existent resources
    if body:
        # Look for script/reference/asset paths mentioned in body
        # Match paths with file extensions to avoid false positives like "scripts/files"
        for match in re.finditer(r'(?:scripts|references|assets)/[\w-]+\.[\w]+', body):
            ref_path = skill_path / match.group(0)
            if not ref_path.exists():
                result.warn(f"SKILL.md references '{match.group(0)}' but file does not exist")

    return result.report(strict)


def main():
    if len(sys.argv) < 2:
        print("Usage: validate_skill.py <skill-directory> [--strict]")
        sys.exit(1)

    skill_path = sys.argv[1]
    strict = "--strict" in sys.argv

    valid = validate_skill(skill_path, strict)
    sys.exit(0 if valid else 1)


if __name__ == "__main__":
    main()
