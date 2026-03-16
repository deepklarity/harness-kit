import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ServiceContext } from '@/contexts/ServiceContext';
import type { HarnessTimeService } from '@/services/harness/HarnessTimeService';
import { KanbanTaskSearch } from './KanbanTaskSearch';

describe('KanbanTaskSearch', () => {
    it('searches and selects the active result with keyboard', async () => {
        const searchTasks = vi.fn().mockResolvedValue([
            {
                taskId: '7',
                title: 'Alpha card',
                status: 'TODO',
                boardId: '3',
                boardName: 'Board A',
                specTitle: 'spec-alpha.md',
            },
        ]);
        const onSelect = vi.fn();

        render(
            <ServiceContext.Provider value={{ searchTasks } as unknown as HarnessTimeService}>
                <KanbanTaskSearch selectedBoard="3" onSelect={onSelect} />
            </ServiceContext.Provider>
        );

        fireEvent.change(screen.getByRole('textbox', { name: /search cards/i }), { target: { value: 'alpha' } });
        await waitFor(() => expect(searchTasks).toHaveBeenCalledWith({
            q: 'alpha',
            scope: 'board',
            boardId: '3',
            limit: 10,
        }));

        fireEvent.keyDown(screen.getByRole('textbox', { name: /search cards/i }), { key: 'Enter' });
        expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ taskId: '7' }), 'board');
    });
});
