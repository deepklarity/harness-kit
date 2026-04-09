import { useState, useEffect, useCallback } from 'react';
import {
    ClipboardList, Wrench, CheckCircle2, FlaskConical, Pin, Zap,
    Copy, Check,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { createElement } from 'react';

const STATUS_KEYWORDS: Record<string, string[]> = {
    backlog: ['backlog'],
    todo: ['to do', 'todo', 'planned'],
    executing: ['executing'],
    doing: ['doing', 'in progress', 'in_progress', 'working', 'active'],
    review: ['review'],
    testing: ['testing', 'qa', 'verify'],
    done: ['done', 'complete', 'finished', 'closed'],
    failed: ['failed', 'error', 'blocked'],
};

export function classifyStatus(listName: string): string {
    const lower = listName.toLowerCase().replace(/[^a-z_\s]/g, '').trim();
    for (const [category, keywords] of Object.entries(STATUS_KEYWORDS)) {
        if (keywords.some(k => lower.includes(k))) return category;
    }
    return 'other';
}

export function formatDuration(ms: number): string {
    if (ms <= 0) return '\u2014';
    const totalSeconds = Math.floor(ms / 1000);
    const s = totalSeconds % 60;
    const m = Math.floor(totalSeconds / 60) % 60;
    const h = Math.floor(totalSeconds / 3600) % 24;
    const d = Math.floor(totalSeconds / 86400);

    if (d > 0) return `${d}d ${h}h`;
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
}

export function formatDate(dateStr: string): string {
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    });
}

export function getStatusColor(status: string): string {
    const category = classifyStatus(status);
    switch (category) {
        case 'backlog': return 'var(--muted-foreground)';
        case 'todo': return 'var(--muted-foreground)';
        case 'executing': return '#22c55e';
        case 'doing': return 'var(--chart-1)';
        case 'review': return 'var(--chart-2)';
        case 'testing': return 'var(--chart-5)';
        case 'done': return 'var(--chart-4)';
        case 'failed': return 'var(--destructive)';
        default: return 'var(--muted-foreground)';
    }
}

export function formatTokens(count: number | undefined): string {
    if (count === undefined || count === 0) return '—'
    if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M tok`
    if (count >= 1_000) return `${(count / 1_000).toFixed(1)}k tok`
    return `${count} tok`
}

export function shortModelName(full: string | undefined): string {
    if (!full) return '—'
    return full.replace(/^claude-/, '').replace(/-\d{8,}$/, '')
}

export function formatMergeStatus(status: string | undefined | null): string {
    if (!status) return '\u2014';
    switch (status) {
        case 'merged': return 'Merged to spec';
        case 'noop': return 'No changes';
        case 'conflict': return 'Merge conflict';
        case 'error': return 'Merge failed';
        case 'pending': return 'Pending merge';
        default: return status;
    }
}

export function formatBranchDisplay(branch: string | undefined | null): string {
    if (!branch) return '\u2014';
    return branch;
}

/** Tiny inline copy button — clipboard icon swaps to checkmark for 1.4s */
export function CopyButton({ text, className = '' }: { text: string; className?: string }) {
    const [copied, setCopied] = useState(false);

    useEffect(() => {
        if (!copied) return;
        const t = window.setTimeout(() => setCopied(false), 1400);
        return () => window.clearTimeout(t);
    }, [copied]);

    const handleCopy = useCallback(async (e: React.MouseEvent) => {
        e.stopPropagation();
        try {
            await navigator.clipboard.writeText(text);
            setCopied(true);
        } catch { /* ignore */ }
    }, [text]);

    return createElement(
        'button',
        {
            onClick: handleCopy,
            className: `inline-flex items-center justify-center size-5 rounded hover:bg-muted/60 transition-colors shrink-0 ${className}`,
            title: copied ? 'Copied!' : 'Copy to clipboard',
            type: 'button' as const,
        },
        copied
            ? createElement(Check, { className: 'size-3 text-emerald-400' })
            : createElement(Copy, { className: 'size-3 text-muted-foreground/50 hover:text-muted-foreground' })
    );
}

/** Copyable monospace command line — text + copy button */
export function CopyableCommand({ command, className = '' }: { command: string; className?: string }) {
    return createElement(
        'div',
        { className: `flex items-center gap-1.5 group ${className}` },
        createElement('code', {
            className: 'text-[10px] font-mono text-muted-foreground bg-secondary/50 px-1.5 py-0.5 rounded truncate',
        }, command),
        createElement(CopyButton, { text: command, className: 'opacity-0 group-hover:opacity-100' })
    );
}

/** Merge status dot color for task table */
export function getMergeStatusDotColor(status: string | undefined | null): string | null {
    if (!status) return null;
    switch (status) {
        case 'merged': return '#22c55e';
        case 'noop': return '#f59e0b';
        case 'pending': return '#9ca3af';
        case 'conflict':
        case 'error': return '#ef4444';
        default: return '#9ca3af';
    }
}

export function getStatusIcon(status: string): LucideIcon {
    const category = classifyStatus(status);
    switch (category) {
        case 'todo':
        case 'backlog':
            return ClipboardList;
        case 'executing':
            return Zap;
        case 'doing':
        case 'review':
            return Wrench;
        case 'done': return CheckCircle2;
        case 'testing': return FlaskConical;
        default: return Pin;
    }
}
