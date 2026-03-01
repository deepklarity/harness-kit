import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { PaginationControls } from './PaginationControls';

describe('PaginationControls', () => {
    it('renders current page and emits page changes', async () => {
        const user = userEvent.setup();
        const onPageChange = vi.fn();
        const onPageSizeChange = vi.fn();
        render(
            <PaginationControls
                count={100}
                page={1}
                pageSize={25}
                onPageChange={onPageChange}
                onPageSizeChange={onPageSizeChange}
            />,
        );

        expect(screen.getByText('Page 1 of 4')).toBeInTheDocument();

        await user.click(screen.getByRole('button', { name: /next/i }));
        expect(onPageChange).toHaveBeenCalledWith(2);
    });
});
