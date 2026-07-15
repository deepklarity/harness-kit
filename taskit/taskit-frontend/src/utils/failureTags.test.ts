import { describe, it, expect } from 'vitest';
import { dedupeFailureTags } from './failureTags';

describe('dedupeFailureTags', () => {
    it('returns a single tag when class and type are identical', () => {
        expect(dedupeFailureTags('stale_execution', 'stale_execution')).toEqual(['stale_execution']);
    });

    it('keeps both when class and type differ', () => {
        expect(dedupeFailureTags('stale_execution', 'timeout')).toEqual(['stale_execution', 'timeout']);
    });

    it('keeps only the class when the raw type is absent', () => {
        expect(dedupeFailureTags('stale_execution', undefined)).toEqual(['stale_execution']);
    });

    it('keeps only the raw type when the class is absent', () => {
        expect(dedupeFailureTags(undefined, 'timeout')).toEqual(['timeout']);
    });

    it('returns an empty list when neither is present', () => {
        expect(dedupeFailureTags(undefined, undefined)).toEqual([]);
    });

    it('returns an empty list when both are empty strings', () => {
        expect(dedupeFailureTags('', '')).toEqual([]);
    });
});
