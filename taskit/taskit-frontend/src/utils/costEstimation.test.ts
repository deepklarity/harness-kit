import { describe, it, expect } from 'vitest';
import { formatCost } from './costEstimation';

describe('formatCost', () => {
    it('formats dollar amount', () => {
        expect(formatCost(1.50)).toBe('$1.50');
    });

    it('formats small cost with 2 decimals', () => {
        expect(formatCost(0.03)).toBe('$0.03');
    });

    it('formats very small cost', () => {
        expect(formatCost(0.001)).toBe('< $0.01');
    });

    it('formats zero as $0.00', () => {
        expect(formatCost(0)).toBe('$0.00');
    });

    it('formats null as em dash', () => {
        expect(formatCost(null)).toBe('—');
    });

    it('formats undefined as em dash', () => {
        expect(formatCost(undefined)).toBe('—');
    });

    it('formats large cost', () => {
        expect(formatCost(12.34)).toBe('$12.34');
    });
});
