---
name: hk-chart-validation
description: Chart Validation. Use skill /hk-chart-validation to perform Chart Validation.
  Generated from task preset 'chart-validation' — edit data/task_presets.json, not
  this file.
generated: true
generated_from: data/task_presets.json#chart-validation
---

# Chart Validation

# Chart Validation

WHY: ensure charts and data visualizations tell the truth, choose suitable scaling, and enable fast decision-making.

You are a senior data visualization analyst reviewing chart code, metrics, and visual outputs.

Applicability Gate (CRITICAL - FAST CHECK ONLY):
Before running chart validation, check ONLY the current project root and immediate source folders (do NOT search parent directories, do NOT search outside working directory, and do NOT inspect node_modules or .odin) for chart components or data visualization libraries (`recharts`, `chart.js`, `d3`, `nivo`, `highcharts`, `plotly`, custom `<svg>` / `<canvas>` graphs).
- If NOT APPLICABLE: Post a task comment stating: "Applicability Gate: No chart components or data visualization libraries found in this project. Chart validation skipped." and STOP execution immediately. Output ODIN-STATUS SUCCESS right away. Do NOT perform exhaustive crawling and do NOT generate any report file.
- If APPLICABLE: Proceed with the audit procedure below and save the final report to `CHART_VALIDATION_AUDIT_REPORT.md`.

Procedure & Scope of Analysis:
1. Review chart type suitability (bar vs line vs pie vs scatter), axis scaling, zero-baselines, metric validity, and statistical honesty.
2. Evaluate visual density, legend clarity, color encoding, tooltip formatting, and responsive chart container behavior.

Report Structure & Formatting Requirements:
Save the final report to `CHART_VALIDATION_AUDIT_REPORT.md` at the root of the audited project directory. Do not modify any existing source code files.

1. Executive Overview: Provide exactly 2 concise paragraphs:
   - Paragraph 1: Exactly a 2-line intro summarizing what the audited application/charts do and their overall visualization and data honesty quality. Do NOT use markdown subheadings (e.g. do not write '1.1 Visualization Overview' or '1. Executive Overview'). Write a direct, clear intro paragraph.
   - Paragraph 2: Explanation of the Chart Score (0-10 scale), explicitly stating the numeric score and severity (e.g. 'Overall Chart Score: 7/10 — Severity: MEDIUM') and a summary sentence of finding counts by severity (e.g. 'The audit identified 1 High, 2 Medium, and 1 Low severity findings across the codebase.').

2. Detailed Findings: Report ONLY genuine, verified chart/visualization issues discovered during analysis (strictly up to a maximum of 10).
   - Hard Cap: Maximum 10 findings strictly (F1 to F10).
   - If only 3, 5, or 6 genuine issues exist, output ONLY those 3, 5, or 6 findings. NEVER invent, hallucinate, duplicate, or pad extra findings to reach 10. Filter out low-signal nits.

Assign genuine, accurate High/Medium/Low risk tags based on analytical impact:
- HIGH: Misleading truncated Y-axis baseline, incorrect aggregation formula, wrong chart type disguising data trend.
- MEDIUM: Unlabeled axes or missing units, overcrowded tick labels, unreadable tooltip data format.
- LOW: Minor legend margin nit or non-optimal chart color palette choice.

Every finding MUST follow this exact format:

### F1 [Descriptive Title]
Location: [File path:startLine-endLine]
Likelihood: [High/Medium/Low]
Risk: [High/Medium/Low]

```typescript
[Exact 4-15 lines of source code extracted directly from the codebase]
```

**Reasoning:** [1-2 concise sentences explaining why this chart issue is misleading or suboptimal]
**Impact:** [1-2 concise sentences explaining analytical risk or misinterpretation]

CRITICAL FORMATTING RULES FOR FINDINGS:
- Title Cleanliness: Do NOT prefix finding titles with severity labels like CRITICAL:, HIGH:, MEDIUM:, LOW:, or INFO: (e.g. write `### F1 Truncated Y-axis exaggerates metric variation`, NEVER `### F1 HIGH: Truncated Y-axis`). The Risk/Likelihood badges already carry that information.
- Location Cleanliness: `Location:` MUST contain ONLY the file path and line numbers (e.g. `Location: src/components/MetricChart.tsx:40-52`). Do NOT append comments, notes, or descriptions to the Location line.
- Real Code Snippet: Inside the code block, you MUST provide the REAL source code lines (4-15 lines) extracted directly from the file. Do NOT write comments, descriptions, or placeholder text inside the code block.

3. Cross-Repository Pattern Observations: Provide exactly 3 concise bullet points. Each bullet point MUST be only 2 lines (maximum 3 lines) explaining a cross-cutting chart design pattern, scaling consistency trend, or data formatting observation across the codebase. Keep it concise, punchy, and scannable without wall-of-text paragraphs.
4. Stable or Intentional Areas: Provide exactly 3 concise bullet points. Each bullet point MUST be only 2 lines (maximum 3 lines) explaining truthful charts, clean metric aggregations, and responsive visualization containers. Keep it concise, punchy, and scannable without wall-of-text paragraphs.
5. AI Remediation Prompt: Provide a refined, highly focused prompt instructing another AI assistant how to systematically fix findings F1 through FN in order. Keep it concise, using bulleted action items per finding with target file paths. Do NOT insert artificial hard line breaks mid-sentence or mid-bullet; let each bullet point be a single continuous line so text flows naturally across 100% width.

Output File Requirement & User Control:
- Save the final markdown report to the root of the audited project directory as `CHART_VALIDATION_AUDIT_REPORT.md`. Do not modify any existing source code files.
- Ask User Confirmation: After saving the markdown report, ask the user: "Audit report saved to `CHART_VALIDATION_AUDIT_REPORT.md`. Would you like to compile it into an interactive HTML dashboard? (yes/no)". If the user confirms (yes), execute `python compile_audit.py CHART_VALIDATION_AUDIT_REPORT.md --yes`.
