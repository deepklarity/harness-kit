import '@testing-library/jest-dom/vitest';

// jsdom does not ship ResizeObserver, but several components (e.g. the
// collapsible comment body) rely on it to measure overflow. Provide a no-op
// stub so rendering the modal with many comments doesn't throw.
class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
}

if (!('ResizeObserver' in globalThis)) {
    (globalThis as { ResizeObserver: typeof ResizeObserver }).ResizeObserver =
        ResizeObserverStub as unknown as typeof ResizeObserver;
}
