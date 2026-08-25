---
name: hk-ai-slop-detection
description: AI Slop Detection. Use skill /hk-ai-slop-detection to perform AI Slop
  Detection. Generated from task preset 'ai-slop-detection' — edit data/task_presets.json,
  not this file.
generated: true
generated_from: data/task_presets.json#ai-slop-detection
---

# AI Slop Detection

WHY: Find AI-generated slop, low-signal cargo-cult code, template duplication, dead state variables, and inconsistent architectural abstractions across the codebase.

You are a senior software engineer performing a forensic audit of a codebase to detect AI-generated slop and code hygiene issues.

Applicability Gate (CRITICAL - FAST CHECK ONLY):
Before running the AI slop audit, check ONLY the current project root and immediate source folders (do NOT search parent directories, do NOT search outside working directory, and do NOT inspect node_modules or .odin) for source code files, components, configuration files, or documentation.
- If NOT APPLICABLE: Post a task comment stating: "Applicability Gate: No source code or executable logic found in this project. AI slop detection skipped." and STOP execution immediately. Output ODIN-STATUS SUCCESS right away. Do NOT perform exhaustive crawling and do NOT generate any report file.
- If APPLICABLE: Proceed with the audit procedure below.

Procedure & Scope of Analysis:
1. Scan source code, components, utility modules, state management, and configuration files directly.
2. Check for repetitive/templated code without abstractions, copy-pasted logic, and unused helper wrappers.
3. Identify dead code, unreferenced variables/handlers, misleading/generic comments, and suppressed linting/type errors.
4. Assess architectural consistency, naming conventions, and unnecessary complexity.

Report Structure & Formatting Requirements:
Save the final report to `AI_SLOP_AUDIT_REPORT.md` at the root of the audited project directory. Do not modify any existing source code files.

1. Executive Overview: Provide exactly 2 concise paragraphs:
   - Paragraph 1: Exactly a 2-line intro summarizing what the audited application/codebase does and its overall AI slop and code hygiene profile. Do NOT use markdown subheadings (e.g. do not write '1.1 Slop Overview' or '1. Executive Overview'). Write a direct, clear intro paragraph.
   - Paragraph 2: Explanation of the Overall Slop Score (0-10 scale), explicitly stating the numeric score and severity (e.g. 'Overall Slop Score: 5/10 — Severity: MEDIUM') and a summary sentence of finding counts by severity (e.g. 'The audit identified 2 High, 3 Medium, and 1 Low severity findings across the codebase.').

2. Detailed Findings: Report ONLY genuine, verified AI slop patterns discovered during analysis (strictly up to a maximum of 10).
   - Hard Cap: Maximum 10 findings strictly (F1 to F10).
   - If only 3, 5, or 6 genuine issues exist, output ONLY those 3, 5, or 6 findings. NEVER invent, hallucinate, duplicate, or pad extra findings to reach 10. Filter out low-signal nits.

Assign genuine, accurate High/Medium/Low risk tags based on real engineering impact:
- HIGH: Dead execution paths, duplicate state machines, cargo-cult mocks posing as real subsystems.
- MEDIUM: Unused helpers/wrappers, multi-copy boilerplate, over-abstracted single-use types.
- LOW: Minor unused imports, generic boilerplate comments, or stylistic inconsistency.

Every finding MUST follow this exact format:

### F1 [Descriptive Title]
Location: [File path:startLine-endLine]
Likelihood: [High/Medium/Low]
Risk: [High/Medium/Low]

```typescript
[Exact 4-15 lines of source code extracted directly from the codebase]
```

**Reasoning:** [1-2 concise sentences explaining why this is slop]
**Impact:** [1-2 concise sentences explaining maintenance risk or developer confusion]

CRITICAL FORMATTING RULES FOR FINDINGS:
- Title Cleanliness: Do NOT prefix finding titles with severity labels like CRITICAL:, HIGH:, MEDIUM:, LOW:, or INFO: (e.g. write `### F1 Dead state variables in component`, NEVER `### F1 HIGH: Dead state variables`). The Risk/Likelihood badges already carry that information.
- Location Cleanliness: `Location:` MUST contain ONLY the file path and line numbers (e.g. `Location: src/components/Sidebar.tsx:12-25`). Do NOT append comments, notes, or descriptions to the Location line.
- Real Code Snippet: Inside the code block, you MUST provide the REAL source code lines (4-15 lines) extracted directly from the file. Do NOT write comments, descriptions, or placeholder text inside the code block.

3. Cross-Repository Pattern Observations: Provide exactly 3 concise bullet points. Each bullet point MUST be only 2 lines (maximum 3 lines) explaining a cross-cutting pattern, duplication trend, or architectural inconsistency across the codebase. Keep it concise, punchy, and scannable without wall-of-text paragraphs.
4. Stable or Intentional Areas: Provide exactly 3 concise bullet points. Each bullet point MUST be only 2 lines (maximum 3 lines) explaining clean, well-architected, and slop-free sections of the codebase. Keep it concise, punchy, and scannable without wall-of-text paragraphs.
5. AI Remediation Prompt: Provide a refined, highly focused prompt instructing another AI assistant how to systematically fix findings F1 through FN in order. Keep it concise, using bulleted action items per finding with target file paths. Do NOT insert artificial hard line breaks mid-sentence or mid-bullet; let each bullet point be a single continuous line so text flows naturally across 100% width.

Output File Requirement & User Control:
- Save the final markdown report to the root of the audited project directory as `AI_SLOP_AUDIT_REPORT.md`. Do not modify any existing source code files.
- Ask User Confirmation: After saving the markdown report, ask the user: "Audit report saved to `AI_SLOP_AUDIT_REPORT.md`. Would you like to compile it into an interactive HTML dashboard? (yes/no)". If the user confirms (yes), execute `python compile_audit.py AI_SLOP_AUDIT_REPORT.md --yes`.
