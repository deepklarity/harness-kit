import { useEffect, useRef } from 'react';
import type { Task, Notification } from '../types';
import type { IntegrationService } from '../services/integration/IntegrationService';
import { useNotifications } from '../contexts/NotificationContext';

const DEBOUNCE_MS = 1500;

/**
 * Auto-refreshes the open task card when a notification arrives for it.
 * Debounces rapid notifications and cancels in-flight fetches to prevent
 * stale data from overwriting fresher data.
 */
export function useTaskCardAutoRefresh(
    selectedTask: Task | null,
    setSelectedTask: (task: Task | ((prev: Task | null) => Task | null)) => void,
    service: IntegrationService,
) {
    const { subscribeToIncoming } = useNotifications();
    const abortRef = useRef<AbortController | null>(null);
    const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(() => {
        const taskId = selectedTask?.id;
        if (!taskId) return;

        // 1. Subscribe to incoming notifications for instant updates on notification triggers
        const unsubscribe = subscribeToIncoming((incoming: Notification[]) => {
            const matches = incoming.some(n => n.task !== null && String(n.task) === taskId);
            if (!matches) return;

            // Clear any pending debounce
            if (timerRef.current) clearTimeout(timerRef.current);

            // Cancel any in-flight fetch
            if (abortRef.current) abortRef.current.abort();

            timerRef.current = setTimeout(() => {
                const controller = new AbortController();
                abortRef.current = controller;

                service.fetchTaskDetail(taskId)
                    .then(detail => {
                        if (!controller.signal.aborted) {
                            setSelectedTask(prev => {
                                if (!prev || prev.id !== taskId) return prev;
                                if (prev.boardName && !detail.boardName) {
                                    detail.boardName = prev.boardName;
                                }
                                return detail;
                            });
                        }
                    })
                    .catch(err => {
                        if (err?.name !== 'AbortError') {
                            console.error('Auto-refresh task detail failed:', err);
                        }
                    });
            }, DEBOUNCE_MS);
        });

        // 2. Setup active polling while task modal is open to stream live execution status,
        // comments, activity logs, and timing updates without requiring close/reopen.
        const isExecuting = ['EXECUTING', 'IN_PROGRESS', 'PENDING', 'RUNNING'].includes(
            (selectedTask?.currentStatus || '').toUpperCase()
        );
        const pollIntervalMs = isExecuting ? 2500 : 6000;

        const intervalId = setInterval(() => {
            const controller = new AbortController();
            abortRef.current = controller;

            service.fetchTaskDetail(taskId)
                .then(detail => {
                    if (!controller.signal.aborted) {
                        setSelectedTask(prev => {
                            if (!prev || prev.id !== taskId) return prev;
                            if (prev.boardName && !detail.boardName) {
                                detail.boardName = prev.boardName;
                            }
                            return detail;
                        });
                    }
                })
                .catch(err => {
                    if (err?.name !== 'AbortError') {
                        // Silently ignore polling network errors
                    }
                });
        }, pollIntervalMs);

        return () => {
            unsubscribe();
            clearInterval(intervalId);
            if (timerRef.current) clearTimeout(timerRef.current);
            if (abortRef.current) abortRef.current.abort();
        };
    }, [selectedTask?.id, selectedTask?.currentStatus, subscribeToIncoming, service, setSelectedTask]);
}
