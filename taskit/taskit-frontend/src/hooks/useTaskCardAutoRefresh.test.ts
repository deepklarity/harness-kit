import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { useTaskCardAutoRefresh } from './useTaskCardAutoRefresh';
import type { Task } from '../types';

vi.mock('../contexts/NotificationContext', () => ({
    useNotifications: () => ({
        subscribeToIncoming: vi.fn(() => vi.fn()),
    }),
}));

describe('useTaskCardAutoRefresh', () => {
    beforeEach(() => {
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it('polls task details periodically when task status is EXECUTING (2.5s interval)', async () => {
        const initialTask = {
            id: '288',
            title: 'Security Audit',
            currentStatus: 'EXECUTING',
            boardId: 'b1',
            createdAt: '2026-08-03T00:00:00Z',
        } as unknown as Task;

        const updatedTask = {
            ...initialTask,
        } as Task;

        const setSelectedTask = vi.fn();
        const service = {
            fetchTaskDetail: vi.fn().mockResolvedValue(updatedTask),
        } as any;

        renderHook(() => useTaskCardAutoRefresh(initialTask, setSelectedTask, service));

        // Before interval fires
        expect(service.fetchTaskDetail).not.toHaveBeenCalled();

        // Fast-forward past 2.5s poll interval
        await act(async () => {
            vi.advanceTimersByTime(2500);
        });

        expect(service.fetchTaskDetail).toHaveBeenCalledWith('288');
    });

    it('polls at steady interval (6s) when task status is COMPLETED', async () => {
        const initialTask = {
            id: '288',
            title: 'Security Audit',
            currentStatus: 'COMPLETED',
            boardId: 'b1',
            createdAt: '2026-08-03T00:00:00Z',
        } as unknown as Task;

        const setSelectedTask = vi.fn();
        const service = {
            fetchTaskDetail: vi.fn().mockResolvedValue(initialTask),
        } as any;

        renderHook(() => useTaskCardAutoRefresh(initialTask, setSelectedTask, service));

        await act(async () => {
            vi.advanceTimersByTime(2500);
        });
        expect(service.fetchTaskDetail).not.toHaveBeenCalled();

        await act(async () => {
            vi.advanceTimersByTime(3500); // reaches 6s total
        });
        expect(service.fetchTaskDetail).toHaveBeenCalledWith('288');
    });
});
