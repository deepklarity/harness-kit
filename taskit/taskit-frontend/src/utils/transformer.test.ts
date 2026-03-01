import { describe, it, expect } from 'vitest'
import { classifyStatus, getStatusColor, getStatusIcon } from './transformer'
import { Zap, Wrench, ClipboardList, CheckCircle2, FlaskConical } from 'lucide-react'

describe('classifyStatus', () => {
  it('classifies EXECUTING as executing', () => {
    expect(classifyStatus('EXECUTING')).toBe('executing')
  })

  it('classifies executing (lowercase) as executing', () => {
    expect(classifyStatus('executing')).toBe('executing')
  })

  it('classifies IN_PROGRESS as doing', () => {
    expect(classifyStatus('IN_PROGRESS')).toBe('doing')
  })

  it('classifies TODO as todo', () => {
    expect(classifyStatus('TODO')).toBe('todo')
  })

  it('classifies DONE as done', () => {
    expect(classifyStatus('DONE')).toBe('done')
  })

  it('classifies FAILED as failed', () => {
    expect(classifyStatus('FAILED')).toBe('failed')
  })

  it('classifies BACKLOG as backlog', () => {
    expect(classifyStatus('BACKLOG')).toBe('backlog')
  })

  it('classifies REVIEW as review', () => {
    expect(classifyStatus('REVIEW')).toBe('review')
  })

  it('classifies TESTING as testing', () => {
    expect(classifyStatus('TESTING')).toBe('testing')
  })
})

describe('getStatusColor', () => {
  it('returns green for executing', () => {
    expect(getStatusColor('EXECUTING')).toBe('#22c55e')
  })
})

describe('getStatusIcon', () => {
  it('returns Zap for executing', () => {
    expect(getStatusIcon('EXECUTING')).toBe(Zap)
  })

  it('returns Wrench for in_progress', () => {
    expect(getStatusIcon('IN_PROGRESS')).toBe(Wrench)
  })

  it('returns ClipboardList for todo', () => {
    expect(getStatusIcon('TODO')).toBe(ClipboardList)
  })

  it('returns CheckCircle2 for done', () => {
    expect(getStatusIcon('DONE')).toBe(CheckCircle2)
  })

  it('returns FlaskConical for testing', () => {
    expect(getStatusIcon('TESTING')).toBe(FlaskConical)
  })
})
