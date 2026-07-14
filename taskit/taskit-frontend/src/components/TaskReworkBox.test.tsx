import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { TaskReworkBox } from './TaskReworkBox'

describe('TaskReworkBox', () => {
    it('renders nothing when the task is not on the shelf (status !== TESTING)', () => {
        const { container } = render(<TaskReworkBox status="IN_PROGRESS" onRework={vi.fn()} />)
        expect(container).toBeEmptyDOMElement()
    })

    it('renders the Rework box for a shelved (TESTING) task', () => {
        render(<TaskReworkBox status="TESTING" onRework={vi.fn()} />)
        expect(screen.getByRole('heading', { name: /rework/i })).toBeInTheDocument()
        expect(screen.getByRole('button', { name: /^rework$/i })).toBeInTheDocument()
    })

    it('submits the typed instruction to onRework', async () => {
        const onRework = vi.fn().mockResolvedValue(undefined)
        render(<TaskReworkBox status="TESTING" onRework={onRework} />)

        const input = screen.getByPlaceholderText(/dark-mode|instruction|sentence/i)
        fireEvent.change(input, { target: { value: 'Add a dark-mode toggle to the settings page.' } })
        fireEvent.click(screen.getByRole('button', { name: /^rework$/i }))

        await waitFor(() => expect(onRework).toHaveBeenCalledWith('Add a dark-mode toggle to the settings page.'))
    })

    it('does not submit an empty instruction', () => {
        const onRework = vi.fn()
        render(<TaskReworkBox status="TESTING" onRework={onRework} />)
        fireEvent.click(screen.getByRole('button', { name: /^rework$/i }))
        expect(onRework).not.toHaveBeenCalled()
    })
})
