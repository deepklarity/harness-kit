import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { SearchBar } from './SearchBar';

describe('SearchBar', () => {
    beforeEach(() => {
        vi.useFakeTimers();
    });
    afterEach(() => {
        vi.useRealTimers();
    });

    it('debounces search callback', () => {
        const onSearchChange = vi.fn();
        render(<SearchBar value="" onSearchChange={onSearchChange} debounceMs={300} />);

        fireEvent.change(screen.getByRole('textbox', { name: /search/i }), { target: { value: 'abc' } });
        vi.advanceTimersByTime(250);
        expect(onSearchChange).not.toHaveBeenCalled();

        vi.advanceTimersByTime(60);
        expect(onSearchChange).toHaveBeenCalledWith('abc');
    });
});
