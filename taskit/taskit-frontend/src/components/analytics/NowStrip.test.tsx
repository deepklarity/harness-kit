import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { NowStrip } from './NowStrip'
import type { FactoryRunningTask, FactoryQueues, MemorySharesBlock } from '@/types'

const NOOP = () => {}

function makeRunningList(): FactoryRunningTask[] {
    return []
}

function makeQueues(): FactoryQueues {
    return { waiting: 0, executing: 0, review: 0, shelf: 0 }
}

describe('NowStrip — memory shares line', () => {
    beforeEach(() => {
        vi.clearAllMocks()
    })

    it('does not render the shares line when memoryShares is absent', () => {
        render(
            <NowStrip
                running={makeRunningList()}
                queues={makeQueues()}
                onTaskClick={NOOP}
            />,
        )
        expect(screen.queryByTestId('memory-shares-line')).not.toBeInTheDocument()
    })

    it('renders the truthful "exec + review = N/M memory shares" line', () => {
        const shares: MemorySharesBlock = {
            budget_mib: 16384,
            reserved_mib: 12288,
            default_vm_mem_mib: 4096,
            max_shares: 4,
            executing_count: 3,
            reflecting_count: 1,
            shares_in_use: 4,
            holders: [
                { task_id: '344', task_title: 'Live A', kind: 'execution', mem_mib: 4096 },
                { task_id: '345', task_title: 'Live B', kind: 'execution', mem_mib: 4096 },
                { task_id: '346', task_title: 'Live C', kind: 'execution', mem_mib: 4096 },
                {
                    task_id: '346',
                    task_title: 'Reflected',
                    kind: 'reflection',
                    mem_mib: 4096,
                    report_id: 12,
                },
            ],
        }
        render(
            <NowStrip
                running={makeRunningList()}
                queues={makeQueues()}
                onTaskClick={NOOP}
                memoryShares={shares}
            />,
        )
        const line = screen.getByTestId('memory-shares-line')
        expect(line.textContent).toMatch(/3 executing \+ 1 review = 4\/4 memory shares/)
    })

    it('hides the shares line when no holders are present', () => {
        const shares: MemorySharesBlock = {
            budget_mib: 16384,
            reserved_mib: 0,
            default_vm_mem_mib: 4096,
            max_shares: 4,
            executing_count: 0,
            reflecting_count: 0,
            shares_in_use: 0,
            holders: [],
        }
        render(
            <NowStrip
                running={makeRunningList()}
                queues={makeQueues()}
                onTaskClick={NOOP}
                memoryShares={shares}
            />,
        )
        expect(screen.queryByTestId('memory-shares-line')).not.toBeInTheDocument()
    })

    it('fires onTaskClick when a holder chip is clicked', () => {
        const onTaskClick = vi.fn()
        const shares: MemorySharesBlock = {
            budget_mib: 8192,
            reserved_mib: 4096,
            default_vm_mem_mib: 4096,
            max_shares: 2,
            executing_count: 1,
            reflecting_count: 0,
            shares_in_use: 1,
            holders: [
                { task_id: '344', task_title: 'Live A', kind: 'execution', mem_mib: 4096 },
            ],
        }
        const { container } = render(
            <NowStrip
                running={makeRunningList()}
                queues={makeQueues()}
                onTaskClick={onTaskClick}
                memoryShares={shares}
            />,
        )
        const chip = container.querySelector('button[title*="Task 344"]') as HTMLButtonElement
        expect(chip).toBeTruthy()
        fireEvent.click(chip)
        expect(onTaskClick).toHaveBeenCalledWith('344')
    })
})
