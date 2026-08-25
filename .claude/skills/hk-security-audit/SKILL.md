---
name: hk-security-audit
description: 'Security Audit. Use skill /hk-security-audit to perform Security Audit
  (Objective: evaluate code security Scope, Findings, credential mounts, token handling,
  sandbox egress po… Generated from task preset ''security-audit'' — edit data/task_presets.json,
  not this file.'
generated: true
generated_from: data/task_presets.json#security-audit
---

# Security Audit

WHY: Evaluate code security, secret leakage, OWASP Top 10 vulnerabilities, API authentication guards, credential mounts, token handling, sandbox egress posture, and subprocess invocations across the codebase.

Applicability Gate (CRITICAL - FAST CHECK ONLY):
Before running the security audit, check ONLY the current project root and immediate source folders (do NOT search parent directories, do NOT search outside working directory, and do NOT inspect node_modules or .odin) for source code files, configuration files, or dependency manifests.
- If NOT APPLICABLE: Post a task comment stating: "Applicability Gate: No source code, configuration files, or dependency manifests found in this project. Security audit skipped." and STOP execution immediately. Output ODIN-STATUS SUCCESS right away. Do NOT perform exhaustive crawling and do NOT generate any report file.
- If APPLICABLE: Proceed with the pre-scan synthesis & audit procedure below.

AUTOMATED TOOL PRE-SCAN & AGENT SYNTHESIS WORKFLOW:
1. Tool Pre-Scan Execution (Completed Pre-Dispatch): The system pre-scanner runs GitLeaks, Bandit, Semgrep, ESLint Security, and Harness risk rules via `scripts/security_audit.py`, generating an initial raw `SECURITY_AUDIT_REPORT.md` at the project root.
2. Read & Synthesize Pre-Scan Report: Read `SECURITY_AUDIT_REPORT.md` at the project root. Synthesize, refine, and format the pre-scan findings in a single pass. Do NOT perform expensive manual codebase crawling for files already scanned.
3. Finalize & Save Report: Consolidate up to 10 key findings into the required format, update `SECURITY_AUDIT_REPORT.md`, and finish.

Report Structure & Formatting Requirements:
Save the final report to `SECURITY_AUDIT_REPORT.md` at the root of the audited project directory. Do not modify any existing source code files.

1. Executive Overview: Provide exactly 2 concise paragraphs:
   - Paragraph 1: Exactly a 2-line intro summarizing what the audited application/codebase does and its overall security posture. Do NOT use markdown subheadings (e.g. do not write '1.1 Security Architecture' or '1. Executive Overview'). Write a direct, clear intro paragraph.
   - Paragraph 2: Explanation of the Security Posture Score (0-10 scale), explicitly stating the numeric score and severity (e.g. 'Overall Security Score: 8/10 — Severity: MEDIUM') and a summary sentence of findings count by severity (e.g. 'The audit identified 1 High, 2 Medium, and 1 Low severity findings across the codebase.').

2. Detailed Findings: Report ONLY genuine, verified security findings discovered during analysis (strictly up to a maximum of 10).
   - Hard Cap: Maximum 10 findings strictly (F1 to F10).
   - If only 3, 5, or 6 genuine issues exist, output ONLY those 3, 5, or 6 findings. NEVER invent, hallucinate, duplicate, or pad extra findings to reach 10. Filter out low-signal nits.

Assign genuine, accurate High/Medium/Low risk tags based on real security impact.

Every finding MUST follow this exact format:

### F1 [Descriptive Title]
Location: [File path:startLine-endLine]
Likelihood: [High/Medium/Low]
Risk: [High/Medium/Low]

```typescript
[Exact 4-15 lines of source code extracted directly from the codebase]
```

**Reasoning:** [1-2 concise sentences explaining the security flaw or risk]
**Impact:** [1-2 concise sentences explaining potential exploit or exfiltration risk]

CRITICAL FORMATTING RULES FOR FINDINGS:
- Title Cleanliness: Do NOT prefix finding titles with severity labels like CRITICAL:, HIGH:, MEDIUM:, LOW:, or INFO: (e.g. write `### F1 Hardcoded API token in production configuration`, NEVER `### F1 CRITICAL: Hardcoded API token`). The Risk/Likelihood badges already carry that information.
- Location Cleanliness: `Location:` MUST contain ONLY the file path and line numbers (e.g. `Location: src/config/auth.ts:42-50`). Do NOT append comments, notes, or descriptions to the Location line.
- Real Code Snippet: Inside the code block, you MUST provide the REAL source code lines (4-15 lines) extracted directly from the file. Do NOT write comments, descriptions, or placeholder text inside the code block.

3. Cross-Repository Pattern Observations: Provide exactly 3 concise bullet points. Each bullet point MUST be only 2 lines (maximum 3 lines) explaining a cross-cutting security pattern or observation across the codebase. Keep it concise, punchy, and scannable without wall-of-text paragraphs.
4. Stable or Intentional Areas: Provide exactly 3 concise bullet points. Each bullet point MUST be only 2 lines (maximum 3 lines) explaining security strengths, hardened configurations, and safe sections. Keep it concise, punchy, and scannable without wall-of-text paragraphs.
5. AI Remediation Prompt: Provide a refined, highly focused prompt instructing another AI assistant how to systematically fix findings F1 through FN in order. Keep it concise, using bulleted action items per finding with target file paths. Do NOT insert artificial hard line breaks mid-sentence or mid-bullet; let each bullet point be a single continuous line so text flows naturally across 100% width.

Output File Requirement & User Control:
- Save the final markdown report to the root of the audited project directory as `SECURITY_AUDIT_REPORT.md`. Do not modify any existing source code files.
- Ask User Confirmation: After saving the markdown report, ask the user: "Audit report saved to `SECURITY_AUDIT_REPORT.md`. Would you like to compile it into an interactive HTML dashboard? (yes/no)". If the user confirms (yes), execute `python compile_audit.py SECURITY_AUDIT_REPORT.md --yes`.

