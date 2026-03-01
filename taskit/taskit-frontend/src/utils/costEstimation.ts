/**
 * Format a cost value for display.
 * - null/undefined → "—"
 * - 0 → "$0.00"
 * - < 0.01 → "< $0.01"
 * - else → "$X.XX"
 *
 * Cost computation is handled by the backend (single source of truth).
 * This module only provides display formatting.
 */
export function formatCost(cost: number | null | undefined): string {
    if (cost === null || cost === undefined) return '—';
    if (cost === 0) return '$0.00';
    if (cost > 0 && cost < 0.01) return '< $0.01';
    return `$${cost.toFixed(2)}`;
}
