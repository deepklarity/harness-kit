import type { Task } from '../types';

export type DispatchBlockSeverity = 'warning' | 'blocked';

export interface DispatchBlockInfo {
    code: string;
    message: string;
    severity: DispatchBlockSeverity;
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
};

const FALLBACK_SEVERITY: DispatchBlockSeverity = 'warning';

/**
 * Read the dispatch_blocked_reason stamp on a task and turn it into a banner
 * the UI can render. Returns null when no reason is set, so callers can do
 * `const info = getDispatchBlockReason(task); info && <Banner .../>`.
 *
 * The codes here match the ones written by `tasks/dag_executor.py` in the
 * backend (F43/F44 dispatch guardrails). New codes added server-side are
 * surfaced as their raw code so nothing is silently hidden from operators.
 */
export function getDispatchBlockReason(task: Pick<Task, 'metadata'>): DispatchBlockInfo | null {
    const code = task.metadata?.dispatch_blocked_reason;
    if (!code || typeof code !== 'string') return null;

    const known = FRIENDLY_MESSAGES[code];
    if (known) {
        return { code, message: known.message, severity: known.severity };
    }

    return {
        code,
        message: `Not dispatched: ${code}`,
        severity: FALLBACK_SEVERITY,
    };
}
