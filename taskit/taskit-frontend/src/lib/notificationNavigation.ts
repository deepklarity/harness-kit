import type { Notification } from '@/types';

export function getNotificationTargetPath(
    notification: Notification,
    fallbackBoard?: string | null,
): string | null {
    const targetBoard = notification.board ? String(notification.board) : fallbackBoard ?? null;

    if (notification.task) {
        const params = new URLSearchParams();
        params.set('taskId', String(notification.task));
        if (targetBoard) params.set('board', targetBoard);
        return `/board?${params.toString()}`;
    }

    if (notification.spec) {
        return targetBoard
            ? `/specs/${notification.spec}?board=${targetBoard}`
            : `/specs/${notification.spec}`;
    }

    return null;
}
