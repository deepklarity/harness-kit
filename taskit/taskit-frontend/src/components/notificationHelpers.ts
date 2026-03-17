import {
    CheckCheck,
    MessageSquare,
    UserPlus,
    HelpCircle,
    CheckCircle,
    ArrowRightLeft,
    Bell,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { NotificationType } from '@/types';

export const TYPE_ICONS: Record<string, LucideIcon> = {
    task_assigned: UserPlus,
    comment_added: MessageSquare,
    status_changed: ArrowRightLeft,
    planning_complete: CheckCircle,
    question_asked: HelpCircle,
    spec_finished: CheckCheck,
};

export const DEFAULT_ICON = Bell;

export const TYPE_META: Record<NotificationType, { label: string; color: string }> = {
    task_assigned:     { label: 'Assigned',  color: 'bg-blue-500/15 text-blue-400' },
    comment_added:     { label: 'Comment',   color: 'bg-amber-500/15 text-amber-400' },
    status_changed:    { label: 'Status',    color: 'bg-purple-500/15 text-purple-400' },
    planning_complete: { label: 'Planning',  color: 'bg-emerald-500/15 text-emerald-400' },
    question_asked:    { label: 'Question',  color: 'bg-orange-500/15 text-orange-400' },
    spec_finished:     { label: 'Spec Done', color: 'bg-teal-500/15 text-teal-400' },
};
