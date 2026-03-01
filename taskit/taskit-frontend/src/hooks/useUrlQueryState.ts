import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';

type QueryValue = string | number | boolean | undefined | null;
type QueryState = Record<string, QueryValue>;

export function useUrlQueryState<T extends QueryState>(defaults: T) {
    const [searchParams, setSearchParams] = useSearchParams();

    const state = useMemo(() => {
        const next = { ...defaults } as Record<string, QueryValue>;
        Object.keys(defaults).forEach((key) => {
            const value = searchParams.get(key);
            if (value === null) return;
            next[key] = value;
        });
        return next as T;
    }, [defaults, searchParams]);

    const setState = useCallback((updates: Partial<T>, replace = true) => {
        setSearchParams(prev => {
            const next = new URLSearchParams(prev);
            Object.entries(updates).forEach(([key, value]) => {
                if (value === undefined || value === null || value === '') {
                    next.delete(key);
                } else {
                    next.set(key, String(value));
                }
            });
            return next;
        }, { replace });
    }, [setSearchParams]);

    const clearAll = useCallback(() => {
        setSearchParams({}, { replace: true });
    }, [setSearchParams]);

    return { state, setState, clearAll, searchParams };
}
