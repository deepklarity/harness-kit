import type { Task } from '../types';

export type DispatchBlockSeverity = 'warning' | 'blocked';

export interface DispatchBlockHolder {
    task_id: string | number;
    task_title: string;
    kind: 'execution' | 'reflection';
}

export interface DispatchBlockInfo {
    code: string;
    message: string;
    severity: DispatchBlockSeverity;
    blocked_by?: DispatchBlockHolder[];
}

const FRIENDLY_MESSAGES: Record<string, { message: string; severity: DispatchBlockSeverity }> = {
    no_assignee: {
        message: 'Not dispatched — no assignee on this task.',
        severity: 'warning',
    },
    deps_not_complete: {
        message: 'Waiting on its dependencies to complete before dispatch.',
        severity: 'warning',
    },
    deps_blocked_failed: {
        message: 'Blocked — a dependency failed.',
        severity: 'blocked',
    },
    no_worktree_no_optin: {
        message: 'Not dispatched — no worktree and board has not opted in to project-root execution.',
        severity: 'blocked',
    },
    concurrency_cap_reached: {
        message: 'Not dispatched — concurrency cap reached.',
        severity: 'warning',
    },
    memory_budget_full: {
        message: 'Waiting for a memory share to free up.',
        severity: 'warning',
    },
};

const FALLBACK_SEVERITY: DispatchBlockSeverity = 'warning';

/**
 * Format the `dispatch_blocked_blocked_by` list (stamped by
 * `tasks/dag_executor._set_dispatch_blocked_reason` when the gate is
 * memory_budget_full) into a clause the UI appends to the banner.
 *
 * Example output: "held by task 344, reflection on 346" — "execution"
 * holders are named with their task id; "reflection" holders are
 * named with "reflection on <id>" so the operator can tell them apart
 * at a glance.
 */
export function describeBlockedBy(
    holders: ReadonlyArray<DispatchBlockHolder>,
): string {
    if (holders.length === 0) return '';
    const parts = holders.slice(0, 3).map(h => {
        if (h.kind === 'reflection') return `reflection on ${h.task_id}`;
        return `task ${h.task_id}`;
    });
    const named = parts.join(', ');
    const overflow = holders.length > 3 ? ` and ${holders.length - 3} more` : '';
    return `held by ${named}${overflow}`;
}

/**
 * Read the dispatch_blocked_reason stamp on a task and turn it into a banner
 * the UI can render. Returns null when no reason is set, so callers can do
 * `const info = getDispatchBlockReason(task); info && <Banner .../>`.
 *
 * For the `memory_budget_full` code the companion `dispatch_blocked_blocked_by`
 * list is read at the same time so the banner can name the tasks holding
 * the memory share that the dispatch is waiting on.
 *
 * Codes match the ones written by `tasks/dag_executor.py` in the backend
 * (F43/F44 dispatch guardrails + memory budget gate). New codes added
 * server-side are surfaced as their raw code so nothing is silently hidden
 * from operators.
 */
export function getDispatchBlockReason(task: Pick<Task, 'metadata'>): DispatchBlockInfo | null {
    const metadata = task.metadata;
    const code = metadata?.dispatch_blocked_reason;
    if (!code || typeof code !== 'string') return null;

    const known = FRIENDLY_MESSAGES[code];
    const blockedByRaw = metadata?.dispatch_blocked_blocked_by;
    const blocked_by: DispatchBlockHolder[] = Array.isArray(blockedByRaw)
        ? blockedByRaw.filter(
              (h): h is DispatchBlockHolder =>
                  !!h &&
                  typeof h === 'object' &&
                  'task_id' in (h as Record<string, unknown>) &&
                  'kind' in (h as Record<string, unknown>) &&
                  ((h as { kind: unknown }).kind === 'execution' ||
                      (h as { kind: unknown }).kind === 'reflection'),
          )
        : [];

    if (known) {
        let message = known.message;
        if (code === 'memory_budget_full' && blocked_by.length > 0) {
            message = `Waiting for a memory share — ${describeBlockedBy(blocked_by)}.`;
        }
        return { code, message, severity: known.severity, blocked_by };
    }

    return {
        code,
        message: `Not dispatched: ${code}`,
        severity: FALLBACK_SEVERITY,
        blocked_by: blocked_by.length > 0 ? blocked_by : undefined,
    };
}
