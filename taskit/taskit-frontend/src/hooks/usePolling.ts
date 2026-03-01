import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

interface UsePollingOptions {
    enabled?: boolean;
    intervalMs?: number;
    immediate?: boolean;
    maxRetryDelayMs?: number;
}

interface UsePollingResult {
    isPaused: boolean;
    isRunning: boolean;
    lastError: Error | null;
    pause: () => void;
    resume: () => void;
    refreshNow: () => Promise<void>;
}

export function usePolling(
    callback: () => Promise<void> | void,
    options: UsePollingOptions = {},
): UsePollingResult {
    const {
        enabled = true,
        intervalMs = Number(import.meta.env.VITE_POLL_INTERVAL_MS || 15000),
        immediate = true,
        maxRetryDelayMs = 120000,
    } = options;

    const callbackRef = useRef(callback);
    const timeoutRef = useRef<number | null>(null);
    const retryRef = useRef(0);
    const [manuallyPaused, setManuallyPaused] = useState(false);
    const [lastError, setLastError] = useState<Error | null>(null);
    const [isRunning, setIsRunning] = useState(false);

    callbackRef.current = callback;

    const clearTimer = useCallback(() => {
        if (timeoutRef.current !== null) {
            window.clearTimeout(timeoutRef.current);
            timeoutRef.current = null;
        }
    }, []);

    const scheduleNext = useCallback((delay: number) => {
        clearTimer();
        timeoutRef.current = window.setTimeout(async () => {
            if (!enabled || manuallyPaused || document.hidden) return;
            setIsRunning(true);
            try {
                await callbackRef.current();
                retryRef.current = 0;
                setLastError(null);
                scheduleNext(intervalMs);
            } catch (error) {
                const err = error instanceof Error ? error : new Error('Polling failed');
                setLastError(err);
                retryRef.current += 1;
                const backoff = Math.min(intervalMs * (2 ** retryRef.current), maxRetryDelayMs);
                scheduleNext(backoff);
            } finally {
                setIsRunning(false);
            }
        }, delay);
    }, [clearTimer, enabled, intervalMs, manuallyPaused, maxRetryDelayMs]);

    const refreshNow = useCallback(async () => {
        if (!enabled || manuallyPaused) return;
        setIsRunning(true);
        try {
            await callbackRef.current();
            retryRef.current = 0;
            setLastError(null);
            scheduleNext(intervalMs);
        } catch (error) {
            const err = error instanceof Error ? error : new Error('Polling failed');
            setLastError(err);
            retryRef.current += 1;
            const backoff = Math.min(intervalMs * (2 ** retryRef.current), maxRetryDelayMs);
            scheduleNext(backoff);
            throw err;
        } finally {
            setIsRunning(false);
        }
    }, [enabled, intervalMs, manuallyPaused, maxRetryDelayMs, scheduleNext]);

    useEffect(() => {
        if (!enabled || manuallyPaused) {
            clearTimer();
            return;
        }
        if (document.hidden) return;
        scheduleNext(immediate ? 0 : intervalMs);
        return clearTimer;
    }, [enabled, manuallyPaused, immediate, intervalMs, scheduleNext, clearTimer]);

    useEffect(() => {
        const onVisibilityChange = () => {
            if (!enabled || manuallyPaused) return;
            if (document.hidden) {
                clearTimer();
            } else {
                scheduleNext(0);
            }
        };
        document.addEventListener('visibilitychange', onVisibilityChange);
        return () => document.removeEventListener('visibilitychange', onVisibilityChange);
    }, [enabled, manuallyPaused, scheduleNext, clearTimer]);

    useEffect(() => clearTimer, [clearTimer]);

    const result = useMemo<UsePollingResult>(() => ({
        isPaused: manuallyPaused || !enabled,
        isRunning,
        lastError,
        pause: () => {
            setManuallyPaused(true);
            clearTimer();
        },
        resume: () => {
            setManuallyPaused(false);
            if (!document.hidden && enabled) {
                scheduleNext(0);
            }
        },
        refreshNow,
    }), [manuallyPaused, enabled, isRunning, lastError, clearTimer, scheduleNext, refreshNow]);

    return result;
}
