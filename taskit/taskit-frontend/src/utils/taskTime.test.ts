import { describe, it, expect, vi } from 'vitest'
import { computeActiveTimes, subtractQuestionPause, questionPauseMs } from './taskTime'

describe('questionPauseMs', () => {
  it('returns accumulated executing_paused_ms', () => {
    expect(questionPauseMs({ executing_paused_ms: 322000 })).toBe(322000)
  })
  it('adds an in-flight pause from question_paused_at', () => {
    vi.useFakeTimers()
    const now = 1718223000000
    vi.setSystemTime(now)
    const pausedAt = new Date(now - 10000).toISOString()
    const v = questionPauseMs({ executing_paused_ms: 5000, question_paused_at: pausedAt })
    expect(v).toBe(15000)
    vi.useRealTimers()
  })
  it('is 0 with no metadata', () => {
    expect(questionPauseMs()).toBe(0)
  })
})

describe('subtractQuestionPause', () => {
  it('subtracts accumulated executing_paused_ms', () => {
    expect(subtractQuestionPause(471000, { executing_paused_ms: 322000 })).toBe(149000)
  })
  it('never goes negative', () => {
    expect(subtractQuestionPause(1000, { executing_paused_ms: 5000 })).toBe(0)
  })
  it('also subtracts an in-flight pause via question_paused_at', () => {
    const pausedAt = new Date(Date.now() - 10000).toISOString()
    const v = subtractQuestionPause(50000, { question_paused_at: pausedAt })
    expect(v).toBeGreaterThan(38000)
    expect(v).toBeLessThanOrEqual(40000)
  })
})

describe('computeActiveTimes', () => {
  it('executing = EXECUTING minus question pause', () => {
    const r = computeActiveTimes({ EXECUTING: 471000 }, { executing_paused_ms: 322000 })
    expect(r.executingTimeMs).toBe(149000)
  })

  it('work time = IN_PROGRESS + EXECUTING(after pause) + REVIEW', () => {
    const r = computeActiveTimes(
      { IN_PROGRESS: 60000, EXECUTING: 471000, REVIEW: 90000 },
      { executing_paused_ms: 322000 },
    )
    expect(r.executingTimeMs).toBe(149000)
    expect(r.workTimeMs).toBe(60000 + 149000 + 90000) // in_progress + executing + review
  })

  it('is continuous across EXECUTING re-entries (caller already summed spans)', () => {
    // span1 333000 + span2 138000 already summed into EXECUTING by computeTimeInStatuses
    const r = computeActiveTimes({ EXECUTING: 471000 }, { executing_paused_ms: 322000 })
    expect(r.executingTimeMs).toBe(149000) // not just the last span (138000)
  })

  it('no metadata → raw values; workTime = in_progress + executing + review', () => {
    const r = computeActiveTimes({ IN_PROGRESS: 1000, EXECUTING: 2000, REVIEW: 3000 })
    expect(r.executingTimeMs).toBe(2000)
    expect(r.workTimeMs).toBe(6000)
  })

  it('never negative', () => {
    const r = computeActiveTimes({ EXECUTING: 1000 }, { executing_paused_ms: 5000 })
    expect(r.executingTimeMs).toBe(0)
    expect(r.workTimeMs).toBe(0)
  })
})
