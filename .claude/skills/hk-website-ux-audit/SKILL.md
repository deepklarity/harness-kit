---
name: hk-website-ux-audit
description: Website UX Audit. Use skill /hk-website-ux-audit to perform Website UX
  Audit. Generated from task preset 'website-ux-audit' — edit data/task_presets.json,
  not this file.
generated: true
generated_from: data/task_presets.json#website-ux-audit
---

# Website UX Audit

WHY: Audit website user experience, navigation flows, accessibility standards, responsive layouts, and visual hierarchy for clarity and usability.

You are a senior UI/UX engineer reviewing website interfaces, accessibility, and user flows.

Applicability Gate (CRITICAL - FAST CHECK ONLY):
Before running website UX audit, check ONLY the current project root and immediate source folders (do NOT search parent directories, do NOT search outside working directory, and do NOT inspect node_modules or .odin) for website pages, UI components, HTML templates, or styling files (`.html`, `.tsx`, `.jsx`, `.vue`, `.svelte`, `.css`).
- If NOT APPLICABLE: Post a task comment stating: "Applicability Gate: No website pages or UI components found in this project. Website UX audit skipped." and STOP execution immediately. Output ODIN-STATUS SUCCESS right away. Do NOT perform exhaustive crawling and do NOT generate any report file.
- If APPLICABLE: Proceed with the audit procedure below.

Procedure & Scope of Analysis:
1. Scan page layouts, navigation hierarchies, responsive breakpoints, and styling systems directly.
2. Check user interaction flows, form validation feedback, error states, loading indicators, and empty states.
3. Evaluate accessibility (aria attributes, keyboard navigation, color contrast ratios) and mobile/tablet viewport responsiveness.
4. Identify broken links, unclear call-to-actions, and confusing layout typography.

Report Structure & Formatting Requirements:
Save the final report to `WEBSITE_UX_AUDIT_REPORT.md` at the root of the audited project directory. Do not modify any existing source code files.

1. Executive Overview: Provide exactly 2 concise paragraphs:
   - Paragraph 1: Exactly a 2-line intro summarizing what the audited application/website does and its overall UI/UX posture. Do NOT use markdown subheadings (e.g. do not write '1.1 UX Overview' or '1. Executive Overview'). Write a direct, clear intro paragraph.
   - Paragraph 2: Explanation of the UX Score (0-10 scale), explicitly stating the numeric score and severity (e.g. 'Overall UX Score: 7/10 — Severity: MEDIUM') and a summary sentence of finding counts by severity (e.g. 'The audit identified 2 High, 3 Medium, and 1 Low severity findings across the codebase.').

2. Detailed Findings: Report ONLY genuine, verified UI/UX defects discovered during analysis (strictly up to a maximum of 10).
   - Hard Cap: Maximum 10 findings strictly (F1 to F10).
   - If only 3, 5, or 6 genuine issues exist, output ONLY those 3, 5, or 6 findings. NEVER invent, hallucinate, duplicate, or pad extra findings to reach 10. Filter out low-signal nits.

Assign genuine, accurate High/Medium/Low risk tags based on user impact:
- HIGH: Broken user flow, missing error/loading feedback causing confusion, unnavigable keyboard focus.
- MEDIUM: Inconsistent color palette usage, poor mobile viewport overflow, missing empty states.
- LOW: Minor alignment nits or subtle font size inconsistencies.

Every finding MUST follow this exact format:

### F1 [Descriptive Title]
Location: [File path:startLine-endLine]
Likelihood: [High/Medium/Low]
Risk: [High/Medium/Low]

```typescript
[Exact 4-15 lines of source code extracted directly from the codebase]
```

**Reasoning:** [1-2 concise sentences explaining the UX/accessibility issue]
**Impact:** [1-2 concise sentences explaining real user impact]

CRITICAL FORMATTING RULES FOR FINDINGS:
- Title Cleanliness: Do NOT prefix finding titles with severity labels like CRITICAL:, HIGH:, MEDIUM:, LOW:, or INFO: (e.g. write `### F1 Missing loading indicator during form submission`, NEVER `### F1 HIGH: Missing loading indicator`). The Risk/Likelihood badges already carry that information.
- Location Cleanliness: `Location:` MUST contain ONLY the file path and line numbers (e.g. `Location: src/components/Form.tsx:30-45`). Do NOT append comments, notes, or descriptions to the Location line.
- Real Code Snippet: Inside the code block, you MUST provide the REAL source code lines (4-15 lines) extracted directly from the file. Do NOT write comments, descriptions, or placeholder text inside the code block.

3. Cross-Repository Pattern Observations: Provide exactly 3 concise bullet points. Each bullet point MUST be only 2 lines (maximum 3 lines) explaining a cross-cutting UX pattern, responsive layout trend, or accessibility theme across the codebase. Keep it concise, punchy, and scannable without wall-of-text paragraphs.
4. Stable or Intentional Areas: Provide exactly 3 concise bullet points. Each bullet point MUST be only 2 lines (maximum 3 lines) explaining intuitive, accessible, and polished UI sections. Keep it concise, punchy, and scannable without wall-of-text paragraphs.
5. AI Remediation Prompt: Provide a refined, highly focused prompt instructing another AI assistant how to systematically fix findings F1 through FN in order. Keep it concise, using bulleted action items per finding with target file paths. Do NOT insert artificial hard line breaks mid-sentence or mid-bullet; let each bullet point be a single continuous line so text flows naturally across 100% width.

Output File Requirement & User Control:
- Save the final markdown report to the root of the audited project directory as `WEBSITE_UX_AUDIT_REPORT.md`. Do not modify any existing source code files.
- Ask User Confirmation: After saving the markdown report, ask the user: "Audit report saved to `WEBSITE_UX_AUDIT_REPORT.md`. Would you like to compile it into an interactive HTML dashboard? (yes/no)". If the user confirms (yes), execute `python compile_audit.py WEBSITE_UX_AUDIT_REPORT.md --yes`.
