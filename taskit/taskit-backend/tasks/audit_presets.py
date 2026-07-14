"""Audit-preset contract: schema validation for audit presets.

Extends the existing ``task_presets.json`` mechanism rather than inventing a
parallel system. A preset may carry an optional ``audit`` block declaring
evidence needs (scripts/endpoints/files/databases), checks, report shape,
and finding-to-task mapping. The full audit *procedure* lives in the
preset's top-level ``description`` (so instantiating it via CreateTaskModal
yields a dispatchable task); the ``audit`` block carries the structured
contract a future runner consumes (see docs/patterns/preset-inventory.md).

Public surface:
    - ``AUDIT_API_VERSION`` — the apiVersion string a valid audit block must use.
    - ``PresetValidationError`` — raised on any contract violation.
    - ``validate_audit_block(preset)`` — validate one preset's audit block
      (no-op when the preset has no ``audit`` key, for backward compat).
    - ``load_presets(filepath=None)`` — load + validate the shipped JSON,
      also enforcing unique preset ids. Used by the ``list_presets`` view.
    - ``validate_preset_shape(preset)`` — the minimal id/title/description/
      category schema every preset must satisfy, hand-added or UI-added
      alike. Used by ``POST /api/presets/manage/``.
    - ``save_presets(data, filepath=None)`` — write the JSON back to disk
      atomically. The only write path — no DB-backed shadow copy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Bump when fields change.
AUDIT_API_VERSION = "audit.harness.kit/v1"

VALID_SEVERITIES = ("P0", "P1", "P2", "P3", "P4")
VALID_PARSE = ("awk", "grep", "json", "stdout", "status")
VALID_SECTION_KINDS = ("line", "table", "list")
VALID_AUTH = ("none", "env_jwt")

DEFAULT_SEVERITY = "P2"


class PresetValidationError(ValueError):
    """Raised when a preset does not satisfy the audit-preset contract."""


def _bad(msg: str) -> PresetValidationError:
    return PresetValidationError(msg)


def _validate_evidence(evidence: Any, pid: str) -> None:
    if not isinstance(evidence, dict):
        raise _bad(f"preset {pid!r}: audit.spec.evidence must be an object")

    # Each evidence bucket is an optional list of item objects.
    buckets = {
        "scripts": _validate_script_item,
        "endpoints": _validate_endpoint_item,
        "files": _validate_file_item,
        "databases": _validate_database_item,
    }
    for bucket, item_validator in buckets.items():
        items = evidence.get(bucket, [])
        if items is None:
            continue
        if not isinstance(items, list):
            raise _bad(f"preset {pid!r}: audit.spec.evidence.{bucket} must be a list")
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                raise _bad(
                    f"preset {pid!r}: audit.spec.evidence.{bucket}[{i}] must be an object"
                )
            item_validator(item, pid, bucket, i)


def _require_keys(item: dict, keys: tuple[str, ...], pid: str, label: str, i: int) -> None:
    for key in keys:
        if key not in item:
            raise _bad(f"preset {pid!r}: {label}[{i}] missing {key!r}")


def _validate_script_item(item: dict, pid: str, bucket: str, i: int) -> None:
    _require_keys(item, ("name", "command"), pid, f"{bucket}", i)
    parse = item.get("parse")
    if parse is not None and parse not in VALID_PARSE:
        raise _bad(
            f"preset {pid!r}: {bucket}[{i}].parse {parse!r} not in {VALID_PARSE}"
        )


def _validate_endpoint_item(item: dict, pid: str, bucket: str, i: int) -> None:
    _require_keys(item, ("method", "path"), pid, f"{bucket}", i)
    auth = item.get("auth")
    if auth is not None and auth not in VALID_AUTH:
        raise _bad(
            f"preset {pid!r}: {bucket}[{i}].auth {auth!r} not in {VALID_AUTH}"
        )
    parse = item.get("parse")
    if parse is not None and parse not in VALID_PARSE:
        raise _bad(
            f"preset {pid!r}: {bucket}[{i}].parse {parse!r} not in {VALID_PARSE}"
        )


def _validate_file_item(item: dict, pid: str, bucket: str, i: int) -> None:
    _require_keys(item, ("path", "check"), pid, f"{bucket}", i)


def _validate_database_item(item: dict, pid: str, bucket: str, i: int) -> None:
    _require_keys(item, ("tool",), pid, f"{bucket}", i)


def _validate_checks(checks: Any, pid: str) -> None:
    if not isinstance(checks, list) or not checks:
        raise _bad(f"preset {pid!r}: audit.spec.checks must be a non-empty list")
    seen: set[str] = set()
    for i, chk in enumerate(checks):
        if not isinstance(chk, dict):
            raise _bad(f"preset {pid!r}: checks[{i}] must be an object")
        _require_keys(chk, ("id", "description", "source", "expects"), pid, "checks", i)
        sev = chk.get("severity_on_fail", DEFAULT_SEVERITY)
        if sev not in VALID_SEVERITIES:
            raise _bad(
                f"preset {pid!r}: checks[{i}].severity_on_fail {sev!r} not in {VALID_SEVERITIES}"
            )
        cid = chk["id"]
        if cid in seen:
            raise _bad(f"preset {pid!r}: duplicate check id {cid!r}")
        seen.add(cid)


def _validate_report(report: Any, pid: str) -> None:
    if not isinstance(report, dict):
        raise _bad(f"preset {pid!r}: audit.spec.report must be an object")
    for req in ("path", "shape"):
        if req not in report:
            raise _bad(f"preset {pid!r}: report missing {req!r}")
    shape = report["shape"]
    if not isinstance(shape, list) or not shape:
        raise _bad(f"preset {pid!r}: report.shape must be a non-empty list")
    section_ids: set[str] = set()
    for i, sec in enumerate(shape):
        if not isinstance(sec, dict):
            raise _bad(f"preset {pid!r}: report.shape[{i}] must be an object")
        _require_keys(sec, ("id", "kind"), pid, "report.shape", i)
        kind = sec["kind"]
        if kind not in VALID_SECTION_KINDS:
            raise _bad(
                f"preset {pid!r}: report.shape[{i}].kind {kind!r} not in {VALID_SECTION_KINDS}"
            )
        sid = sec["id"]
        if sid in section_ids:
            raise _bad(f"preset {pid!r}: duplicate report section id {sid!r}")
        section_ids.add(sid)


def _validate_findings(findings: Any, pid: str) -> None:
    if not isinstance(findings, dict):
        raise _bad(f"preset {pid!r}: audit.spec.findings must be an object")
    scale = findings.get("severity_scale")
    if not isinstance(scale, list) or not scale:
        raise _bad(f"preset {pid!r}: findings.severity_scale must be a non-empty list")
    for s in scale:
        if s not in VALID_SEVERITIES:
            raise _bad(
                f"preset {pid!r}: findings.severity_scale entry {s!r} not in {VALID_SEVERITIES}"
            )
    template = findings.get("task_template")
    if not isinstance(template, dict):
        raise _bad(f"preset {pid!r}: findings.task_template must be an object")
    _require_keys(
        template,
        ("title_prefix", "description_template", "default_agent", "proof_path"),
        pid,
        "findings.task_template",
        0,
    )


def validate_audit_block(preset: dict) -> None:
    """Validate the optional ``audit`` block on a preset.

    Presets without an ``audit`` key are valid (backward compatibility —
    every existing prompt preset still passes). Raises
    :class:`PresetValidationError` on contract violation.
    """
    audit = preset.get("audit")
    if audit is None:
        return
    pid = preset.get("id", "<unknown>")
    if not isinstance(audit, dict):
        raise _bad(f"preset {pid!r}: audit must be an object")
    if audit.get("apiVersion") != AUDIT_API_VERSION:
        raise _bad(
            f"preset {pid!r}: audit.apiVersion must be {AUDIT_API_VERSION!r}, "
            f"got {audit.get('apiVersion')!r}"
        )
    triggers = audit.get("triggers")
    if not isinstance(triggers, list) or not triggers:
        raise _bad(f"preset {pid!r}: audit.triggers must be a non-empty list")
    spec = audit.get("spec")
    if not isinstance(spec, dict):
        raise _bad(f"preset {pid!r}: audit.spec must be an object")
    _validate_evidence(spec.get("evidence", {}), pid)
    _validate_checks(spec.get("checks", []), pid)
    _validate_report(spec.get("report", {}), pid)
    _validate_findings(spec.get("findings", {}), pid)


def _default_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "task_presets.json"


def load_presets(filepath: str | Path | None = None) -> dict:
    """Load and validate ``task_presets.json``.

    Returns the parsed dict. Non-audit presets pass through unchanged; every
    preset with an ``audit`` block is validated against the contract, and
    preset ids must be unique across the file. Raises
    :class:`PresetValidationError` on any violation.
    """
    path = Path(filepath) if filepath is not None else _default_path()
    with open(path) as f:
        data = json.load(f)

    presets = data.get("presets", [])
    if not isinstance(presets, list):
        raise _bad("task_presets.json: 'presets' must be a list")

    seen_ids: set[str] = set()
    for preset in presets:
        if not isinstance(preset, dict):
            raise _bad("task_presets.json: every preset must be an object")
        validate_audit_block(preset)
        pid = preset.get("id")
        if pid is None:
            raise _bad("task_presets.json: preset missing 'id'")
        if pid in seen_ids:
            raise _bad(f"task_presets.json: duplicate preset id {pid!r}")
        seen_ids.add(pid)

    return data


REQUIRED_PRESET_FIELDS = ("id", "title", "description", "category")


def validate_preset_shape(preset: Any) -> None:
    """Validate the minimal shape every preset must satisfy.

    This is the one schema that gates both hand-added (edited directly in
    ``task_presets.json``) and UI-added (via ``POST /api/presets/manage/``)
    presets, so every preset stays exportable by construction — the skill
    exporter (``odin export_skills``) only ever needs ``id``/``title``/
    ``description`` to render a valid skill. A preset carrying an ``audit``
    block is additionally checked against the full audit-preset contract.
    """
    if not isinstance(preset, dict):
        raise _bad("preset must be an object")
    for field in REQUIRED_PRESET_FIELDS:
        value = preset.get(field)
        if not isinstance(value, str) or not value.strip():
            raise _bad(f"preset missing required field {field!r}")
    if "audit" in preset:
        validate_audit_block(preset)


def save_presets(data: dict, filepath: str | Path | None = None) -> None:
    """Write ``task_presets.json`` back to disk, atomically (write + rename)."""
    path = Path(filepath) if filepath is not None else _default_path()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2) + "\n")
    tmp_path.replace(path)
