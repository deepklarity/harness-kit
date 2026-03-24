import type { LucideIcon } from 'lucide-react';

export const CHART_COLORS = [
    'var(--chart-1)',
    'var(--chart-2)',
    'var(--chart-3)',
    'var(--chart-4)',
    'var(--chart-5)',
    'var(--chart-1)',
];

export const customTooltipStyle = {
    backgroundColor: 'var(--popover)',
    border: '1px solid var(--border)',
    borderRadius: 'calc(var(--radius) - 2px)',
    padding: '8px 12px',
    boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)',
    color: 'var(--popover-foreground)',
    fontSize: '12px',
};

export interface ChartCardProps {
    title: string;
    icon: LucideIcon;
    children: React.ReactNode;
    className?: string;
}
