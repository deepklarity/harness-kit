import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { MarkdownRenderer } from './MarkdownRenderer'

describe('MarkdownRenderer — intra-word underscores', () => {
    it('renders the reported identifier literally without italic mangling', () => {
        const reported = 'test_real_wave3_reflections_replay'
        const { container } = render(<MarkdownRenderer text={reported} />)
        expect(container.querySelectorAll('em')).toHaveLength(0)
        expect(container.textContent).toContain(reported)
    })

    it('does not italicize simple snake_case identifiers', () => {
        const { container } = render(<MarkdownRenderer text="foo_bar_baz" />)
        expect(container.querySelectorAll('em')).toHaveLength(0)
        expect(container.textContent).toContain('foo_bar_baz')
    })

    it('does not bold double-underscore identifiers', () => {
        const { container } = render(<MarkdownRenderer text="a__b__c" />)
        expect(container.querySelectorAll('strong')).toHaveLength(0)
        expect(container.textContent).toContain('a__b__c')
    })

    it('still renders asterisk emphasis', () => {
        const { container } = render(<MarkdownRenderer text="run *fast* now" />)
        const em = container.querySelector('em')
        expect(em).not.toBeNull()
        expect(em?.textContent).toBe('fast')
        expect(container.textContent).toContain('run')
        expect(container.textContent).toContain('now')
    })

    it('still renders underscore emphasis when surrounded by spaces', () => {
        const { container } = render(<MarkdownRenderer text="plain _italic_ plain" />)
        const em = container.querySelector('em')
        expect(em).not.toBeNull()
        expect(em?.textContent).toBe('italic')
    })

    it('still renders double-underscore bold when surrounded by spaces', () => {
        const { container } = render(<MarkdownRenderer text="plain __bold__ plain" />)
        const strong = container.querySelector('strong')
        expect(strong).not.toBeNull()
        expect(strong?.textContent).toBe('bold')
    })

    it('keeps underscores literal inside asterisk emphasis', () => {
        const { container } = render(<MarkdownRenderer text="**bold_identifier**" />)
        const strong = container.querySelector('strong')
        expect(strong).not.toBeNull()
        expect(strong?.textContent).toBe('bold_identifier')
    })
})
