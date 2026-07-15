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

describe('MarkdownRenderer — ordered list numbering', () => {
    it('numbers a contiguous list 1, 2, 3', () => {
        const { container } = render(<MarkdownRenderer text={'1. one\n2. two\n3. three'} />)
        const ol = container.querySelector('ol')
        expect(ol).not.toBeNull()
        expect(ol?.getAttribute('start')).toBe('1')
        expect(ol?.querySelectorAll('li')).toHaveLength(3)
    })

    it('keeps numbering continuous across blank lines (loose list)', () => {
        // Blank lines between items used to split the list into three
        // single-item <ol>s, each restarting at "1.".
        const { container } = render(
            <MarkdownRenderer text={'1. one\n\n2. two\n\n3. three'} />,
        )
        // Exactly one ordered list survives.
        expect(container.querySelectorAll('ol')).toHaveLength(1)
        expect(container.querySelector('ol')?.querySelectorAll('li')).toHaveLength(3)
    })

    it('keeps numbering continuous when a warning line sits between twins', () => {
        // Mirrors the memory-twins card: a quoted warning blurb between
        // numbered items must not reset the count to 1, 1, 1. The list
        // splits across the blockquote, but the second run carries its real
        // start number (2) so the rendered numbers read 1, 2, 3.
        const md = [
            '**Memory — closest finished twins:**',
            '1. #359 "twin one" — FAILED',
            '',
            '> estimate: high',
            '',
            '2. #184 "twin two" — DONE',
            '',
            '3. #153 "twin three" — DONE',
        ].join('\n')
        const { container } = render(<MarkdownRenderer text={md} />)
        const ols = container.querySelectorAll('ol')
        expect(ols).toHaveLength(2)
        expect(ols[0].getAttribute('start')).toBe('1')
        expect(ols[1].getAttribute('start')).toBe('2')
        expect(container.querySelectorAll('ol li')).toHaveLength(3)
    })

    it('does not merge a numbered list with a following paragraph', () => {
        const { container } = render(
            <MarkdownRenderer text={'1. one\n2. two\n\nEstimate: ~24 min'} />
        )
        expect(container.querySelectorAll('ol')).toHaveLength(1)
        expect(container.querySelector('ol')?.querySelectorAll('li')).toHaveLength(2)
        expect(container.textContent).toContain('Estimate')
    })
})
