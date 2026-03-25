import { useEffect, useRef } from 'react';

interface ShortcutActions {
    openCommandPalette: (initialQuery?: string) => void;
    openShortcutsModal?: () => void;
    createTask: () => void;
    navigateTo: (path: string) => void;
}

export function useGlobalShortcuts(actions: ShortcutActions, suppressSingleKeys: boolean) {
    const pendingGRef = useRef(false);
    const gTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(() => {
        const handler = (e: KeyboardEvent) => {
            const isMod = e.metaKey || e.ctrlKey;
            const target = e.target as HTMLElement;
            const isInput = target.tagName === 'INPUT' || target.tagName === 'TEXTAREA'
                || target.tagName === 'SELECT' || target.isContentEditable;

            // Cmd+K / Ctrl+K — always fires, even in inputs
            if (isMod && e.key === 'k') {
                e.preventDefault();
                e.stopPropagation();
                actions.openCommandPalette();
                return;
            }

            // All single-key shortcuts suppressed when typing or modal is open
            if (isInput || suppressSingleKeys) return;

            if (e.key === 'n' && !isMod && !e.shiftKey && !e.altKey) {
                e.preventDefault();
                actions.createTask();
                return;
            }
            if (e.key === '?' || (e.shiftKey && e.key === '/')) {
                e.preventDefault();
                actions.openShortcutsModal?.();
                return;
            }

            // "g then <key>" vim-style navigation (500ms window)
            if (e.key === 'g' && !isMod) {
                pendingGRef.current = true;
                if (gTimerRef.current) clearTimeout(gTimerRef.current);
                gTimerRef.current = setTimeout(() => { pendingGRef.current = false; }, 500);
                return;
            }
            if (pendingGRef.current) {
                pendingGRef.current = false;
                if (gTimerRef.current) clearTimeout(gTimerRef.current);
                const NAV: Record<string, string> = {
                    b: '/board',
                    s: '/specs',
                    d: '/stats',
                    n: '/notifications',
                    t: '/settings',
                };
                const path = NAV[e.key];
                if (path) { e.preventDefault(); actions.navigateTo(path); }
                return;
            }
        };

        document.addEventListener('keydown', handler, true);
        return () => document.removeEventListener('keydown', handler, true);
    }, [actions, suppressSingleKeys]);
}
