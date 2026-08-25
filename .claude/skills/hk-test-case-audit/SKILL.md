---
name: hk-test-case-audit
description: Test Case Audit. Use skill /hk-test-case-audit to perform Test Case Audit
  (auditing test suite and test coverage for gaps; outputs TEST_CASE_AUDIT_REPORT.md).
  Generated from task preset 'test-case-audit' — edit data/task_presets.json, not
  this file.
generated: true
generated_from: data/task_presets.json#test-case-audit
---

# Test Case Audit

# Test Case Audit

You are a senior software engineer performing a forensic audit of a codebase to evaluate its test suite and test coverage.

Your task is analysis only. Do NOT modify, refactor, or rewrite anything.

Applicability Gate (CRITICAL - FAST CHECK ONLY):
Before running test case audit, check ONLY the current project root and immediate source folders (do NOT search parent directories, do NOT search outside working directory, and do NOT inspect node_modules or .odin) for source code or test files.
- If NOT APPLICABLE: Post a task comment stating: "Applicability Gate: No source code or test files found in this project. Test case audit skipped." and STOP execution immediately. Output ODIN-STATUS SUCCESS right away. Do NOT perform exhaustive crawling and do NOT generate any report file.
- If APPLICABLE: Proceed with the audit procedure below.

Objective: Analyze the repository's test cases, evaluate testing depth, check for safe execution, identify duplicates or gaps, and produce a single well-structured Markdown report identifying strengths and critical gaps.

Scope of Analysis: Test files (*.test.*, *.spec.*, test_*.py, etc.), test execution scripts, mock setups, assertion patterns, and coverage configuration.

Possible Indicators of Testing Gaps:
- Missing unit tests for core logical functions/views
- Test cases that make live API/LLM calls (unsafe setups)
- Copied/duplicate mock setups or assertion structures
- Happy-path only testing without checking error/negative paths

Report Structure & Formatting Requirements:
Save the final report to `TEST_CASE_AUDIT_REPORT.md` at the root of the audited project directory. Do not modify any existing source code files.

1. Executive Overview: Provide exactly 2 concise paragraphs:
   - Paragraph 1: Exactly a 2-line intro summarizing what the audited application/codebase does and its overall test suite maturity. Do NOT use markdown subheadings (e.g. do not write '1.1 Test Suite Overview' or '1. Executive Overview'). Write a direct, clear intro paragraph.
   - Paragraph 2: Explanation of the Test Quality Score (0-10 scale), explicitly stating the numeric score and severity (e.g. 'Test Quality Score: 7/10 — Quality: HIGH') and a summary sentence of finding counts by severity (e.g. 'The audit identified 0 High, 2 Medium, and 5 Low severity findings across the codebase.').

2. Detailed Findings: Report ONLY genuine, verified testing gaps discovered during analysis (strictly up to a maximum of 10).
   - Hard Cap: Maximum 10 findings strictly (F1 to F10).
   - If only 3, 5, or 6 genuine issues exist, output ONLY those 3, 5, or 6 findings. NEVER invent, hallucinate, duplicate, or pad extra findings to reach 10. Filter out low-signal nits.

Every finding MUST follow this exact format:

### F1 [Descriptive Title]
Location: [File path:startLine-endLine]
Likelihood: [High/Medium/Low]
Risk: [High/Medium/Low]

```typescript
[Exact 4-15 lines of source code extracted directly from the codebase]
```

**Reasoning:** [1-2 concise sentences explaining the testing gap]
**Impact:** [1-2 concise sentences explaining risk/regression potential]

CRITICAL FORMATTING RULES FOR FINDINGS:
- Title Cleanliness: Do NOT prefix finding titles with severity labels like CRITICAL:, HIGH:, MEDIUM:, LOW:, or INFO: (e.g. write `### F1 Missing error boundary tests for auth flow`, NEVER `### F1 HIGH: Missing error boundary tests`). The Risk/Likelihood badges already carry that information.
- Location Cleanliness: `Location:` MUST contain ONLY the file path and line numbers (e.g. `Location: tests/unit/auth.test.ts:45-55`). Do NOT append comments, notes, or descriptions to the Location line.
- Real Code Snippet: Inside the code block, you MUST provide the REAL source code lines (4-15 lines) extracted directly from the file. Do NOT write comments, descriptions, or placeholder text inside the code block.

3. Cross-Repository Pattern Observations: Provide exactly 3 concise bullet points. Each bullet point MUST be only 2 lines (maximum 3 lines) explaining a cross-cutting testing pattern, coverage gap, or architectural mock challenge. Keep it concise, punchy, and scannable without wall-of-text paragraphs.
4. Stable or Intentional Areas: Provide exactly 3 concise bullet points. Each bullet point MUST be only 2 lines (maximum 3 lines) explaining strong test suites, clean mock factories, and reliable test setups. Keep it concise, punchy, and scannable without wall-of-text paragraphs.
5. AI Remediation Prompt: Provide a refined, highly focused prompt instructing another AI assistant how to systematically fix findings F1 through FN in order. Keep it concise, using bulleted action items per finding with target file paths. Do NOT insert artificial hard line breaks mid-sentence or mid-bullet; let each bullet point be a single continuous line so text flows naturally across 100% width.

Output File Requirement & User Control:
- Save the final markdown report to the root of the audited project directory as `TEST_CASE_AUDIT_REPORT.md`. Do not modify any existing source code files.
- Ask User Confirmation: After saving the markdown report, ask the user: "Audit report saved to `TEST_CASE_AUDIT_REPORT.md`. Would you like to compile it into an interactive HTML dashboard? (yes/no)". If the user confirms (yes), execute `python compile_audit.py TEST_CASE_AUDIT_REPORT.md --yes`.
