from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "docs" / "loops" / "_REGISTRY.md"

REQUIRED_LOOPS = {
    "plan -> review -> exec",
    "TDD wave",
    "reflection -> retry",
    "worktree merge",
    "compounding (/hk-compound)",
    "slop audit",
    "autonomy audit",
    "benchmark (planned)",
}

REQUIRED_FIELDS = (
    "Trigger",
    "State location",
    "Exit criteria",
    "Closure metric",
    "Owner",
)


def _sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current:
            sections[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


def _field(section: str, name: str) -> str:
    prefix = f"- {name}:"
    for line in section.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""


def _backticked_paths(line: str) -> list[str]:
    parts = line.split("`")
    return [parts[i] for i in range(1, len(parts), 2) if "/" in parts[i] or parts[i].endswith(".md")]


def test_loop_registry_exists_with_required_seed_loops():
    assert REGISTRY.exists(), "docs/loops/_REGISTRY.md must exist"
    sections = _sections(REGISTRY.read_text())
    missing = REQUIRED_LOOPS.difference(sections)
    assert not missing, f"missing loop registry entries: {sorted(missing)}"


def test_each_loop_entry_has_contract_fields_real_state_and_metric():
    sections = _sections(REGISTRY.read_text())
    assert sections, "registry must contain loop entries"

    for name, section in sections.items():
        for field_name in REQUIRED_FIELDS:
            value = _field(section, field_name)
            assert value, f"{name} missing {field_name}"

        state = _field(section, "State location")
        paths = _backticked_paths(state)
        assert paths, f"{name} state location must cite at least one repository path"
        for path in paths:
            assert (ROOT / path).exists(), f"{name} cites missing state path: {path}"

        metric = _field(section, "Closure metric")
        assert any(char.isdigit() for char in metric), f"{name} closure metric must be measurable: {metric}"
        assert _field(section, "Owner") in {"human", "agent", "scheduled"}, f"{name} owner must use the registry enum"
