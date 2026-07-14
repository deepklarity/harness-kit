import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ReflectionReportViewer } from './ReflectionReportViewer'
import type { ReflectionReport } from '../types'

function makeReport(overrides: Partial<ReflectionReport> = {}): ReflectionReport {
    return {
        id: 1,
        task: 1,
        reviewer_agent: 'claude',
        reviewer_model: 'claude-opus-4-6',
        custom_prompt: '',
        context_selections: [],
        requested_by: 'user@example.com',
        status: 'COMPLETED',
        quality_assessment: '',
        slop_detection: '',
        improvements: '',
        agent_optimization: '',
        verdict: 'NEEDS_WORK',
        verdict_summary: 'Some normal summary',
        raw_output: '',
        execution_trace: '',
        assembled_prompt: '',
        duration_ms: 1000,
        token_usage: { total_tokens: 100, input_tokens: 50, output_tokens: 50 },
        error_message: '',
        created_at: new Date().toISOString(),
        completed_at: new Date().toISOString(),
        task_title: 'Sample task',
        ...overrides,
    }
}

describe('ReflectionReportViewer — ERROR verdict', () => {
    it('renders ERROR verdict banner with reviewer error summary', () => {
        const report = makeReport({
            verdict: 'ERROR',
            verdict_summary: 'Reviewer failure — no verdict in output. The reviewer produced no output.',
        })
        render(<ReflectionReportViewer reports={[report]} />)
        const banner = screen.getByTestId('reflection-error-banner')
        expect(banner).toBeInTheDocument()
        expect(banner.textContent).toMatch(/Reviewer failure/)
    })

    it('shows raw reviewer head in the banner when verdict_summary is short', () => {
        const report = makeReport({
            verdict: 'ERROR',
            verdict_summary: 'invalid config: volume name "logs" must match regex',
        })
        render(<ReflectionReportViewer reports={[report]} />)
        const banner = screen.getByTestId('reflection-error-banner')
        expect(banner.textContent).toMatch(/invalid config: volume name/)
    })

    it('renders ERROR badge with distinct styling from NEEDS_WORK', () => {
        const errorReport = makeReport({ verdict: 'ERROR' })
        const needsWorkReport = makeReport({
            id: 2,
            verdict: 'NEEDS_WORK',
            verdict_summary: 'Add more tests',
        })
        const { container } = render(
            <ReflectionReportViewer reports={[errorReport, needsWorkReport]} />
        )
        const errorBadge = container.querySelector('[data-testid="reflection-error-banner"]')
        expect(errorBadge).toBeInTheDocument()
        // ERROR verdict badge is present alongside the banner
        const allVerdictBadges = container.querySelectorAll('[data-verdict]')
        const verdictValues = Array.from(allVerdictBadges).map(b => b.getAttribute('data-verdict'))
        expect(verdictValues).toContain('ERROR')
    })

    it('does NOT render error banner for NEEDS_WORK verdict', () => {
        const report = makeReport({
            verdict: 'NEEDS_WORK',
            verdict_summary: 'Some normal summary',
        })
        render(<ReflectionReportViewer reports={[report]} />)
        expect(screen.queryByTestId('reflection-error-banner')).not.toBeInTheDocument()
    })

    it('does NOT render error banner for PASS verdict', () => {
        const report = makeReport({
            verdict: 'PASS',
            verdict_summary: 'All checks passed',
        })
        render(<ReflectionReportViewer reports={[report]} />)
        expect(screen.queryByTestId('reflection-error-banner')).not.toBeInTheDocument()
    })
})
