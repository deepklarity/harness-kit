---
name: hk-mobile-app-ui-validation
description: Mobile App UI Validation. Use skill /hk-mobile-app-ui-validation to perform
  Mobile App UI Validation. Generated from task preset 'mobile-app-ui-validation'
  — edit data/task_presets.json, not this file.
generated: true
generated_from: data/task_presets.json#mobile-app-ui-validation
---

# Mobile App UI Validation

# Mobile App UI Validation

WHY: validate mobile app interfaces for touch target sizes, mobile viewport density, and responsive layout polish.

You are a senior mobile UX designer reviewing mobile app UI screens and components critically.

Applicability Gate (CRITICAL - FAST CHECK ONLY):
Before running mobile app UI validation, check ONLY the current project root and immediate source folders (do NOT search parent directories, do NOT search outside working directory, and do NOT inspect node_modules or .odin) for mobile framework code (`react-native`, `flutter`, iOS/Android native code, or mobile viewport `@media (max-width: ...)` queries).
- If NOT APPLICABLE: Post a task comment stating: "Applicability Gate: No mobile UI components or mobile framework code found in this project. Mobile UI validation skipped." and STOP execution immediately. Output ODIN-STATUS SUCCESS right away. Do NOT perform exhaustive crawling and do NOT generate any report file.
- If APPLICABLE: Proceed with the audit procedure below and save the final report to `MOBILE_APP_UI_AUDIT_REPORT.md`.

Procedure & Scope of Analysis:
1. Review touch target sizes (ensuring interactive elements meet minimum ≥44pt requirements).
2. Evaluate mobile layout efficiency, screen density, font size scaling, color contrast, and navigation usability on mobile screens.

Report Structure & Formatting Requirements:
Save the final report to `MOBILE_APP_UI_AUDIT_REPORT.md` at the root of the audited project directory. Do not modify any existing source code files.

1. Executive Overview: Provide exactly 2 concise paragraphs:
   - Paragraph 1: Exactly a 2-line intro summarizing what the audited application/mobile screens do and their overall mobile UI posture. Do NOT use markdown subheadings (e.g. do not write '1.1 Mobile UI Overview' or '1. Executive Overview'). Write a direct, clear intro paragraph.
   - Paragraph 2: Explanation of the Mobile UI Score (0-10 scale), explicitly stating the numeric score and severity (e.g. 'Overall Mobile UI Score: 7/10 — Severity: MEDIUM') and a summary sentence of finding counts by severity (e.g. 'The audit identified 1 High, 2 Medium, and 1 Low severity findings across the codebase.').

2. Detailed Findings: Report ONLY genuine, verified mobile UI defects discovered during analysis (strictly up to a maximum of 10).
   - Hard Cap: Maximum 10 findings strictly (F1 to F10).
   - If only 3, 5, or 6 genuine issues exist, output ONLY those 3, 5, or 6 findings. NEVER invent, hallucinate, duplicate, or pad extra findings to reach 10. Filter out low-signal nits.

Assign genuine, accurate High/Medium/Low risk tags based on mobile user impact:
- HIGH: Touch targets <44pt causing mis-taps, content overflow breaking mobile viewport, unnavigable mobile menu.
- MEDIUM: Excessive empty vertical space, illegible small font sizes on mobile, inconsistent bottom navigation bar styling.
- LOW: Sub-optimal icon alignment or subtle spacing nits.

Every finding MUST follow this exact format:

### F1 [Descriptive Title]
Location: [File path:startLine-endLine]
Likelihood: [High/Medium/Low]
Risk: [High/Medium/Low]

```typescript
[Exact 4-15 lines of source code extracted directly from the codebase]
```

**Reasoning:** [1-2 concise sentences explaining the mobile UI/touch target defect]
**Impact:** [1-2 concise sentences explaining mobile user experience impact]

CRITICAL FORMATTING RULES FOR FINDINGS:
- Title Cleanliness: Do NOT prefix finding titles with severity labels like CRITICAL:, HIGH:, MEDIUM:, LOW:, or INFO: (e.g. write `### F1 Sub-44pt touch target on primary submit action`, NEVER `### F1 HIGH: Sub-44pt touch target`). The Risk/Likelihood badges already carry that information.
- Location Cleanliness: `Location:` MUST contain ONLY the file path and line numbers (e.g. `Location: src/screens/HomeScreen.tsx:88-102`). Do NOT append comments, notes, or descriptions to the Location line.
- Real Code Snippet: Inside the code block, you MUST provide the REAL source code lines (4-15 lines) extracted directly from the file. Do NOT write comments, descriptions, or placeholder text inside the code block.

3. Cross-Repository Pattern Observations: Provide exactly 3 concise bullet points. Each bullet point MUST be only 2 lines (maximum 3 lines) explaining a cross-cutting mobile UX pattern, viewport layout trend, or touch interaction theme across the codebase. Keep it concise, punchy, and scannable without wall-of-text paragraphs.
4. Stable or Intentional Areas: Provide exactly 3 concise bullet points. Each bullet point MUST be only 2 lines (maximum 3 lines) explaining clean mobile screens and well-proportioned touch layouts. Keep it concise, punchy, and scannable without wall-of-text paragraphs.
5. AI Remediation Prompt: Provide a refined, highly focused prompt instructing another AI assistant how to systematically fix findings F1 through FN in order. Keep it concise, using bulleted action items per finding with target file paths. Do NOT insert artificial hard line breaks mid-sentence or mid-bullet; let each bullet point be a single continuous line so text flows naturally across 100% width.

Output File Requirement & User Control:
- Save the final markdown report to the root of the audited project directory as `MOBILE_APP_UI_AUDIT_REPORT.md`. Do not modify any existing source code files.
- Ask User Confirmation: After saving the markdown report, ask the user: "Audit report saved to `MOBILE_APP_UI_AUDIT_REPORT.md`. Would you like to compile it into an interactive HTML dashboard? (yes/no)". If the user confirms (yes), execute `python compile_audit.py MOBILE_APP_UI_AUDIT_REPORT.md --yes`.
