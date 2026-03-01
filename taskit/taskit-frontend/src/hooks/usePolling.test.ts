import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { usePolling } from './usePolling';

describe('usePolling', () => {
    beforeEach(() => {
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it('supports manual pause and resume', async () => {
        const callback = vi.fn().mockResolvedValue(undefined);
        const { result } = renderHook(() => usePolling(callback, { intervalMs: 1000, immediate: true }));

        await act(async () => {
            vi.runOnlyPendingTimers();
        });
        expect(callback).toHaveBeenCalledTimes(1);

        act(() => result.current.pause());
        await act(async () => {
            vi.advanceTimersByTime(5000);
        });
        expect(callback).toHaveBeenCalledTimes(1);

        act(() => result.current.resume());
        await act(async () => {
            vi.runOnlyPendingTimers();
        });
        expect(callback).toHaveBeenCalledTimes(2);
    });

    it('pauses when tab is hidden and resumes when visible', async () => {
        const callback = vi.fn().mockResolvedValue(undefined);
        let hidden = false;
        Object.defineProperty(document, 'hidden', {
            configurable: true,
            get: () => hidden,
        });

        renderHook(() => usePolling(callback, { intervalMs: 1000, immediate: false }));

        hidden = true;
        act(() => document.dispatchEvent(new Event('visibilitychange')));
        await act(async () => {
            vi.advanceTimersByTime(2000);
        });
        expect(callback).toHaveBeenCalledTimes(0);

        hidden = false;
        act(() => document.dispatchEvent(new Event('visibilitychange')));
        await act(async () => {
            vi.runOnlyPendingTimers();
        });
        expect(callback).toHaveBeenCalledTimes(1);
    });
});
