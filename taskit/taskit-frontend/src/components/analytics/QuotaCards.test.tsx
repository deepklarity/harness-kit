import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QuotaCards } from './QuotaCards';
import type { ProviderQuota } from '../../types';

function makeQuota(overrides: Partial<ProviderQuota>): ProviderQuota {
    return {
        provider: 'Test',
        plan: null,
        usage_pct: null,
        used: null,
        limit: null,
        remaining: null,
        unit: 'requests',
        reset_date: null,
        state: null,
        raw: null,
        error: null,
        last_fetched: null,
        ...overrides,
    };
}

describe('QuotaCards — error surfacing', () => {
    it('shows the error reason instead of silent "No data"', () => {
        const data = [makeQuota({
            provider: 'Claude Code',
            error: 'No OAuth token found (check Keychain)',
            usage_pct: null,
        })];
        render(<QuotaCards data={data} />);
        expect(screen.getByText(/No OAuth token found/i)).toBeInTheDocument();
        expect(screen.queryByText('No data')).not.toBeInTheDocument();
    });

    it('shows a real percentage when usage_pct is present', () => {
        const data = [makeQuota({
            provider: 'Codex (OpenAI)',
            usage_pct: 42,
            error: null,
        })];
        render(<QuotaCards data={data} />);
        expect(screen.getByText('42%')).toBeInTheDocument();
    });
});

describe('QuotaCards — staleness timestamp', () => {
    it('shows "as of HH:MM" when last_fetched is present', () => {
        const ts = '2025-07-11T14:02:00';
        const data = [makeQuota({
            provider: 'Claude Code',
            error: 'No OAuth token found',
            last_fetched: ts,
        })];
        const { container } = render(<QuotaCards data={data} />);
        // The timestamp must be visible somewhere on the card.
        expect(container.textContent).toMatch(/as of/i);
    });
});

describe('QuotaCards — bar honesty', () => {
    it('does not render a filled progress bar when usage_pct is null', () => {
        const data = [makeQuota({
            provider: 'MiniMax',
            usage_pct: null,
            error: 'MiniMax not configured',
        })];
        const { container } = render(<QuotaCards data={data} />);
        // When there's no data, no progress-bar fill element should exist —
        // never a misleading fill that looks like real usage.
        expect(container.querySelector('[style*="width"]')).toBeNull();
    });
});
