import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ReplyBox } from './ReplyBox'

describe('ReplyBox', () => {
    it('defaults to a "Reply" button label', () => {
        render(<ReplyBox onSubmit={vi.fn()} />)
        expect(screen.getByRole('button', { name: /^reply$/i })).toBeInTheDocument()
    })

    it('renders a custom button label (task #259: reused as the Rework box)', () => {
        render(<ReplyBox onSubmit={vi.fn()} buttonLabel="Rework" />)
        expect(screen.getByRole('button', { name: /^rework$/i })).toBeInTheDocument()
    })

    it('submits the trimmed content under the custom label and clears the box', async () => {
        const onSubmit = vi.fn().mockResolvedValue(undefined)
        render(<ReplyBox onSubmit={onSubmit} buttonLabel="Rework" placeholder="One sentence…" />)

        const input = screen.getByPlaceholderText('One sentence…')
        fireEvent.change(input, { target: { value: '  Add a dark-mode toggle.  ' } })
        fireEvent.click(screen.getByRole('button', { name: /^rework$/i }))

        await waitFor(() => expect(onSubmit).toHaveBeenCalledWith('Add a dark-mode toggle.'))
        await waitFor(() => expect((input as HTMLInputElement).value).toBe(''))
    })
})
