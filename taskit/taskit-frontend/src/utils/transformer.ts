import {
    ClipboardList, Wrench, CheckCircle2, FlaskConical, Pin, Zap,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

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
    if (h > 0) return `${h}h ${m}m ${s}s`;
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
