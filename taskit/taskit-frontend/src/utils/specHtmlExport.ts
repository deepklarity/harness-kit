import type { Spec, Task } from '../types';
import { formatDuration, formatDate } from './transformer';
import { formatCost } from './costEstimation';

// ---------------------------------------------------------------------------
// Builds a single self-contained HTML document for a spec and all its tasks,
// for sharing offline (no network, no JS). Pure function of (spec, opts).
// Scope: essentials + final outcome/failure. All user text is HTML-escaped.
// ---------------------------------------------------------------------------

const DASH = '—';

export interface BuildSpecHtmlOptions {
    /** Board display name (not present on Spec; supplied by the caller). */
    boardName?: string;
    /** Export timestamp; injectable for deterministic tests. */
    exportedAt?: Date;
}

function esc(value: unknown): string {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/** Escape for an attribute value, preserving newlines as &#10; for tooltips. */
function escAttr(value: string): string {
    return esc(value).replace(/\n/g, '&#10;');
}

/** "IN_PROGRESS" -> "In progress", "DONE" -> "Done". */
function prettyStatus(status: string): string {
    const s = (status || '').replace(/_/g, ' ').trim().toLowerCase();
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : DASH;
}


function specStatusLabel(status?: string): string {
    switch (status) {
        case 'planning': return 'Planning';
        case 'planning_complete': return 'Planning complete';
        case 'planning_failed': return 'Planning failed';
        case 'active': return 'Active';
        default: return status || DASH;
    }
}

function tokensInOut(usage: Task['usage']): string {
    if (!usage) return DASH;
    const i = usage.input_tokens;
    const o = usage.output_tokens;
    if (i == null && o == null) return DASH;
    return `${(i ?? 0).toLocaleString()} in / ${(o ?? 0).toLocaleString()} out`;
}

function agentLabel(task: Task): string {
    return task.assignees && task.assignees.length ? task.assignees[0] : '';
}

/** Largest status bucket for the visible value + a full breakdown for the tooltip. */
function timeInStatus(task: Task): { visible: string; tooltip: string } | null {
    const entries = Object.entries(task.timeInStatuses || {}).filter(([, ms]) => ms > 0);
    if (!entries.length) return null;
    entries.sort((a, b) => b[1] - a[1]);
    const tooltip = entries.map(([s, ms]) => `${prettyStatus(s)}: ${formatDuration(ms)}`).join('\n');
    const [topStatus, topMs] = entries[0];
    return { visible: `${prettyStatus(topStatus)} ${formatDuration(topMs)}`, tooltip };
}

/** Long descriptions get a JS-free "Read more / Show less" toggle (CSS checkbox + line-clamp). */
const DESC_CLAMP_LEN = 240;
function renderDescription(task: Task): string {
    if (!task.description) return '';
    const text = esc(task.description);
    if (task.description.length <= DESC_CLAMP_LEN) {
        return `<div class="desc"><span class="lbl">Description</span>${text}</div>`;
    }
    const id = `desc-${String(task.id).replace(/[^a-zA-Z0-9_-]/g, '')}`;
    return `<div class="desc"><span class="lbl">Description</span>` +
        `<input type="checkbox" id="${id}" class="desc-toggle">` +
        `<div class="desc-text">${text}</div>` +
        `<label for="${id}" class="desc-more more">Read more</label>` +
        `<label for="${id}" class="desc-more less">Show less</label>` +
        `</div>`;
}

function renderTask(task: Task, tasksById: Map<string, Task>): string {
    const status = (task.currentStatus || '').toUpperCase();
    const isFailed = status === 'FAILED';
    const isDone = status === 'DONE';

    const title = esc(task.title || task.name || 'Untitled task');
    const badgeClass = isDone ? 'b-done' : isFailed ? 'b-failed' : 'b-prog';
    const badgeLabel = esc(prettyStatus(task.currentStatus));

    const agent = agentLabel(task);
    const model = task.modelName || '';
    const agentText = agent && model ? `${esc(agent)} · <span class="mono">${esc(model)}</span>`
        : agent ? esc(agent) : DASH;
    const agentSummary = agent ? esc(agent) : DASH;

    const durationStr = task.executingTimeMs > 0 ? esc(formatDuration(task.executingTimeMs)) : DASH;
    const cost = esc(formatCost(task.estimatedCostUsd));
    const tokens = esc(tokensInOut(task.usage));

    const tis = timeInStatus(task);
    const tisCell = tis
        ? `<span class="hoverable" title="${escAttr(tis.tooltip)}">${esc(tis.visible)} &#9432;</span>`
        : DASH;

    const desc = renderDescription(task);
    const dependencies = (task.dependsOn || []).map(id => tasksById.get(id) || { id, title: `Task #${id}`, currentStatus: 'UNKNOWN' } as Task);
    const dependencyHtml = dependencies.length
        ? `<div class="dependencies"><span class="lbl">Depends on</span>${dependencies.map(dependency =>
            `<span class="dep ${['FAILED', 'BLOCKED'].includes(dependency.currentStatus.toUpperCase()) ? 'dep-bad' : ''}">Task ${esc(dependency.idShort || dependency.id)}: ${esc(dependency.title || dependency.name)} · ${esc(prettyStatus(dependency.currentStatus))}${['FAILED', 'BLOCKED'].includes(dependency.currentStatus.toUpperCase()) ? ' — blocks this task' : ''}</span>`
        ).join('')}</div>`
        : '';

    let outcome: string;
    if (isDone) {
        outcome = `<div class="outcome ok"><span class="label">✓ Complete</span> — this task is finished.</div>`;
    } else if (isFailed) {
        const md = (task.metadata || {}) as Record<string, unknown>;
        const reason = md.last_failure_reason ? esc(md.last_failure_reason) : 'Not recorded';
        const origin = md.last_failure_origin ? esc(md.last_failure_origin) : DASH;
        const type = md.last_failure_type ? esc(md.last_failure_type) : DASH;
        outcome =
            `<div class="outcome fail"><span class="label">⚠ Needs attention</span>` +
            `<div class="fail-grid">` +
            `<span class="fk">What happened</span><span>${reason}</span>` +
            `<span class="fk">Where</span><span>${origin}</span>` +
            `<span class="fk">Category</span><span>${type}</span>` +
            `</div></div>`;
    } else if (status === 'TESTING') {
        outcome = `<div class="outcome ok"><span class="label">✓ Complete</span> — final testing is underway.</div>`;
    } else {
        const STATES: Record<string, { label: string; note: string; active: boolean }> = {
            EXECUTING: { label: 'Working now', note: 'an agent is actively working on it.', active: true },
            IN_PROGRESS: { label: 'In progress', note: 'work is underway.', active: true },
            REVIEW: { label: 'Under review', note: 'waiting for a review decision.', active: true },
            TODO: { label: 'To do', note: 'not started yet.', active: false },
            BACKLOG: { label: 'Backlog', note: 'planned for later.', active: false },
        };
        const st = STATES[status] || { label: prettyStatus(task.currentStatus), note: 'no final outcome recorded.', active: false };
        const color = st.active ? 'var(--prog-ink)' : 'var(--muted)';
        outcome = `<div class="outcome"><span class="label" style="color:${color}">${esc(st.label)}</span> — ${esc(st.note)}</div>`;
    }

    const proofComment = (task.comments || []).find((c: any) => c.commentType === 'proof');
    const proofAttachments = proofComment?.fileAttachments || [];
    const proofLinks = proofAttachments.map((fa: any) => 
        `<a href="task_${task.idShort}/${esc(fa.originalFilename)}" style="color: #4f46e5; text-decoration: none; font-weight: 500;">🖼️ Proof: ${esc(fa.originalFilename)}</a>`
    ).join('\n        ');

    const traceComment = (task.comments || []).find((c: any) =>
        c.content && (c.content.includes('"type":"step_start"') || c.content.includes('"type":"step_finish"') || c.content.includes('trace:execution_jsonl'))
    );
    const timelineLink = traceComment 
        ? `<a href="task_${task.idShort}/timeline_${task.idShort}.png" style="color: #4f46e5; text-decoration: none; font-weight: 500;">📊 Timeline Image</a>`
        : '';

    return `
  <details class="task${isFailed ? ' failed' : ''}"${isFailed ? ' open' : ''}>
    <summary>
      <span class="chev">&#9654;</span>
      <span class="s-title">Task ${esc(task.idShort)}: ${title}</span>
      <span class="badge ${badgeClass}">${badgeLabel}</span>
      <span class="s-meta">${agentSummary} · ${durationStr} · ${cost}</span>
    </summary>
    <div class="body">
      ${desc}
      ${dependencyHtml}
      <dl class="kv">
        <dt>Assigned to</dt><dd>${agentText}</dd>
        <dt>Where time went</dt><dd>${tisCell}</dd>
        <dt>Execution time</dt><dd>${durationStr}</dd>
        <dt>Tokens used</dt><dd>${tokens}</dd>
        <dt>Estimated cost</dt><dd>${cost}</dd>
      </dl>
      ${outcome}
      <div style="margin-top: 14px; padding-top: 10px; border-top: 1px dashed var(--line); display: flex; flex-wrap: wrap; gap: 14px; font-size: 12.5px;">
        <span style="color: var(--muted); font-weight: 600;">Included files:</span>
        <a href="task_${task.idShort}/task_report.md" style="color: #4f46e5; text-decoration: none; font-weight: 500;">📄 Full task report</a>
        ${proofLinks}
        ${timelineLink}
      </div>
    </div>
  </details>`;
}

const STYLE = `
  :root {
    --bg: #f7f8fa; --card: #ffffff; --ink: #1a1d21; --muted: #6b7280; --line: #e5e7eb;
    --ok-bg: #ecfdf5; --ok-ink: #047857; --ok-line: #a7f3d0;
    --fail-bg: #fef2f2; --fail-ink: #b91c1c; --fail-line: #fecaca;
    --prog-bg: #eff6ff; --prog-ink: #1d4ed8; --prog-line: #bfdbfe;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink);
    font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    -webkit-font-smoothing: antialiased; }
  .wrap { max-width: 860px; margin: 0 auto; padding: 32px 20px 64px; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
  header.spec { border-bottom: 1px solid var(--line); padding-bottom: 20px; margin-bottom: 22px; }
  .eyebrow { color: var(--muted); font-size: 12px; letter-spacing: .04em; text-transform: uppercase; }
  h1 { font-size: 22px; margin: 6px 0 10px; }
  .meta { color: var(--muted); font-size: 13px; display: flex; flex-wrap: wrap; gap: 6px 16px; }
  .meta b { color: var(--ink); font-weight: 600; }
  .totals { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-top: 18px; }
  .stat { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; }
  .stat .k { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .04em; }
  .stat .v { font-size: 18px; font-weight: 650; margin-top: 4px; }
  .stat .sub { color: var(--muted); font-size: 11px; margin-top: 3px; }
  h2.section { font-size: 13px; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; margin: 26px 0 12px; }
  details.task { background: var(--card); border: 1px solid var(--line); border-radius: 12px; margin-bottom: 10px; overflow: hidden; }
  details.task.failed { border-color: var(--fail-line); }
  summary { list-style: none; cursor: pointer; display: flex; align-items: center; gap: 10px; padding: 13px 16px; }
  summary::-webkit-details-marker { display: none; }
  summary:hover { background: #fafafa; }
  .chev { color: var(--muted); font-size: 11px; transition: transform .15s; flex: 0 0 auto; }
  details[open] .chev { transform: rotate(90deg); }
  .s-title { font-weight: 640; font-size: 15px; }
  .s-meta { margin-left: auto; color: var(--muted); font-size: 12.5px; white-space: nowrap; }
  .badge { font-size: 11px; font-weight: 650; padding: 3px 9px; border-radius: 999px; white-space: nowrap; border: 1px solid transparent; flex: 0 0 auto; }
  .b-done { background: var(--ok-bg); color: var(--ok-ink); border-color: var(--ok-line); }
  .b-failed { background: var(--fail-bg); color: var(--fail-ink); border-color: var(--fail-line); }
  .b-prog { background: var(--prog-bg); color: var(--prog-ink); border-color: var(--prog-line); }
  .body { padding: 4px 16px 16px; border-top: 1px solid var(--line); }
  .desc { margin: 14px 0 16px; }
  .desc .lbl { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .04em; display: block; margin-bottom: 4px; }
  .desc-toggle { position: absolute; opacity: 0; width: 0; height: 0; pointer-events: none; }
  .desc-text { display: -webkit-box; -webkit-line-clamp: 3; line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; white-space: pre-wrap; }
  .desc-toggle:checked ~ .desc-text { -webkit-line-clamp: unset; line-clamp: unset; overflow: visible; }
  .desc-more { display: inline-block; margin-top: 4px; color: #4f46e5; cursor: pointer; font-size: 12px; font-weight: 600; }
  .desc-more.less { display: none; }
  .desc-toggle:checked ~ .desc-more.more { display: none; }
  .desc-toggle:checked ~ .desc-more.less { display: inline-block; }
  dl.kv { display: grid; grid-template-columns: max-content 1fr; gap: 7px 18px; margin: 0; font-size: 13px; }
  dl.kv dt { color: var(--muted); }
  dl.kv dd { margin: 0; }
  .hoverable { border-bottom: 1px dotted #9ca3af; cursor: help; }
  .outcome { margin-top: 14px; padding: 10px 12px; border-radius: 8px; font-size: 13px; }
  .outcome.ok { background: var(--ok-bg); color: var(--ok-ink); }
  .outcome.fail { background: var(--fail-bg); color: var(--fail-ink); }
  .outcome .label { font-weight: 700; }
  .fail-grid { margin-top: 8px; display: grid; grid-template-columns: max-content 1fr; gap: 4px 14px; color: var(--ink); }
  .fail-grid .fk { color: var(--fail-ink); font-weight: 600; }
  .dependencies { margin: 14px 0; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .dependencies .lbl { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .04em; margin-right: 4px; }
  .dep { font-size: 12px; border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; }
  .dep-bad { color: var(--fail-ink); background: var(--fail-bg); border-color: var(--fail-line); }
  .current-outcome { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 14px; margin: 18px 0; }
  .current-outcome p { margin: 4px 0 0; }
  footer { margin-top: 36px; padding-top: 16px; border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; }
  @media print { body { background: #fff; } details.task, .stat { break-inside: avoid; } }
`;

export function buildSpecHtml(spec: Spec, opts: BuildSpecHtmlOptions = {}): string {
    const cs = spec.costSummary;
    const branch = (spec.metadata && (spec.metadata as Record<string, unknown>).branch) as string | undefined;
    const exportedAt = opts.exportedAt ?? new Date();

    const totalCost = esc(formatCost(cs?.total_cost_usd));
    const totalTokens = cs && cs.total_tokens != null ? esc(cs.total_tokens.toLocaleString()) : DASH;
    const tokenSub = cs && (cs.total_input_tokens != null || cs.total_output_tokens != null)
        ? `${(cs.total_input_tokens ?? 0).toLocaleString()} in · ${(cs.total_output_tokens ?? 0).toLocaleString()} out`
        : '';
    const totalDuration = cs && cs.total_duration_ms != null ? esc(formatDuration(cs.total_duration_ms)) : DASH;

    const metaItems: string[] = [
        `<span>Status: <b>${esc(specStatusLabel(spec.status))}</b></span>`,
    ];
    if (opts.boardName) metaItems.push(`<span>Board: <b>${esc(opts.boardName)}</b></span>`);
    if (branch) metaItems.push(`<span>Branch: <b class="mono">${esc(branch)}</b></span>`);
    metaItems.push(`<span>Created: <b>${esc(formatDate(spec.createdAt))}</b></span>`);

    const tasksById = new Map(spec.tasks.map(task => [task.id, task]));
    const done = spec.tasks.filter(task => ['DONE', 'TESTING'].includes(task.currentStatus.toUpperCase())).length;
    const failed = spec.tasks.filter(task => task.currentStatus.toUpperCase() === 'FAILED').length;
    const active = spec.tasks.length - done - failed;
    const blocker = spec.tasks.find(task => task.currentStatus.toUpperCase() === 'FAILED' || task.needsHuman || task.dependsOn?.some(id => {
        const dependency = tasksById.get(id);
        return dependency && ['FAILED', 'BLOCKED'].includes(dependency.currentStatus.toUpperCase());
    }));
    const blockerText = blocker
        ? blocker.failureHumanReason || blocker.failureSuggestedAction || `${blocker.title || blocker.name} needs attention.`
        : 'Nothing is currently blocking this work.';
    const tasksHtml = spec.tasks.length
        ? spec.tasks.map(task => renderTask(task, tasksById)).join('\n')
        : `<div class="stat" style="text-align:center;color:var(--muted)">No tasks in this spec.</div>`;

    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spec #${esc(spec.id)} — ${esc(spec.title)}</title>
<style>${STYLE}</style>
</head>
<body>
<div class="wrap">
  <header class="spec">
    <div class="eyebrow">Spec #${esc(spec.id)} · TaskIt export</div>
    <h1>${esc(spec.title)}</h1>
    <div class="meta">${metaItems.join('\n      ')}</div>
    <div class="totals">
      <div class="stat"><div class="k">Tasks</div><div class="v">${spec.tasks.length}</div></div>
      <div class="stat"><div class="k">Total cost</div><div class="v">${totalCost}</div></div>
      <div class="stat"><div class="k">Total tokens</div><div class="v">${totalTokens}</div>${tokenSub ? `<div class="sub">${esc(tokenSub)}</div>` : ''}</div>
      <div class="stat"><div class="k">Total duration</div><div class="v">${totalDuration}</div></div>
    </div>
    <section class="current-outcome">
      <strong>At a glance</strong>
      <p>${done} completed · ${failed} need attention · ${active} still open</p>
      <p><b>Main blocker:</b> ${esc(blockerText)}</p>
    </section>
  </header>

  <h2 class="section">Tasks (${spec.tasks.length})</h2>
${tasksHtml}

  <footer>
    Read-only TaskIt snapshot created on ${esc(formatDate(exportedAt.toISOString()))}. Everything needed to read this report is included here.
  </footer>
</div>
</body>
</html>`;
}

/** Build the spec HTML and trigger a browser download of a single .html file. */
export function downloadSpecHtml(spec: Spec, opts: BuildSpecHtmlOptions = {}): void {
    const html = buildSpecHtml(spec, opts);
    const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const slug = (spec.title || 'spec')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '')
        .slice(0, 50) || 'spec';
    const a = document.createElement('a');
    a.href = url;
    a.download = `spec-${spec.id}-${slug}.html`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}
