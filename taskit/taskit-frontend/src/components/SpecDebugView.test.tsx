import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SpecDebugView } from './SpecDebugView'
import { ServiceContext } from '../contexts/ServiceContext'

// Mock the DagView since it uses dagre which is hard to test in jsdom
vi.mock('./DagView', () => ({
  DagView: ({ tasks }: { tasks: unknown[] }) => (
    <div data-testid="dag-view">DagView ({tasks.length} tasks)</div>
  ),
}))

const mockDiagnosticResponse = {
  id: 1,
  odin_id: 'sp_001',
  title: 'Test Spec',
  source: 'inline',
  content: '',
  abandoned: false,
  board_id: 1,
  board_name: 'Sprint 1',
  metadata: {},
  created_at: '2024-01-01T00:00:00Z',
  tasks: [
    {
      id: 1,
      board_id: 1,
      title: 'Task A',
      description: 'First task',
      priority: 'MEDIUM',
      status: 'DONE',
      assignee: { id: 1, name: 'Alice', email: 'alice@test.com' },
      created_at: '2024-01-01T00:00:00Z',
      created_by: 'alice@test.com',
      last_updated_at: '2024-01-01T01:00:00Z',
      depends_on: [],
      metadata: { last_duration_ms: 5000 },
      history: [
        {
          id: 1, task_id: 1, field_name: 'created',
          old_value: '', new_value: 'Task created',
          changed_at: '2024-01-01T00:00:00Z', changed_by: 'alice@test.com',
        },
        {
          id: 2, task_id: 1, field_name: 'status',
          old_value: 'TODO', new_value: 'DONE',
          changed_at: '2024-01-01T01:00:00Z', changed_by: 'alice@test.com',
        },
      ],
      comments: [
        {
          id: 1, task_id: 1, author_email: 'alice@test.com',
          author_label: 'alice', content: 'Completed in 5s',
          attachments: [], created_at: '2024-01-01T01:00:00Z',
        },
      ],
    },
    {
      id: 2,
      board_id: 1,
      title: 'Task B',
      description: 'Second task',
      priority: 'HIGH',
      status: 'FAILED',
      created_at: '2024-01-01T00:00:00Z',
      created_by: 'alice@test.com',
      last_updated_at: '2024-01-01T02:00:00Z',
      depends_on: ['1'],
      history: [],
      comments: [],
    },
  ],
}

function createMockService() {
  return {
    baseUrl: 'http://localhost:8000',
    authHeaders: vi.fn().mockResolvedValue({}),
    // Satisfy the IntegrationService interface minimally
    name: 'test',
    setTokenProvider: vi.fn(),
    getAuthState: vi.fn(),
    login: vi.fn(),
    logout: vi.fn(),
    saveCredentials: vi.fn(),
    handleCallback: vi.fn(),
    fetchData: vi.fn(),
    fetchTaskDetail: vi.fn(),
    updateTaskAssignees: vi.fn(),
    createBoard: vi.fn(),
    createTask: vi.fn(),
    updateTask: vi.fn(),
    getAvailableStatuses: vi.fn(),
    createUser: vi.fn(),
    updateUser: vi.fn(),
    deleteUser: vi.fn(),
    fetchUsers: vi.fn(),
    deleteTask: vi.fn(),
    deleteSpec: vi.fn(),
    clearBoard: vi.fn(),
    addBoardMembers: vi.fn(),
    removeBoardMembers: vi.fn(),
    getLabels: vi.fn(),
    createLabel: vi.fn(),
    addTaskLabel: vi.fn(),
    removeTaskLabel: vi.fn(),
    getCachedLabels: vi.fn(),
    addComment: vi.fn(),
    fetchSpecs: vi.fn(),
    fetchSpecDetail: vi.fn(),
    fetchSpecDiagnostic: vi.fn(),
    cloneSpec: vi.fn(),
  }
}

describe('SpecDebugView', () => {
  let mockService: ReturnType<typeof createMockService>

  beforeEach(() => {
    mockService = createMockService()
    // Mock global fetch for the diagnostic endpoint
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(mockDiagnosticResponse),
    })
  })

  const renderWithProviders = (specId: string) => {
    return render(
      <MemoryRouter>
        <ServiceContext.Provider value={mockService as any}>
          <SpecDebugView specId={specId} onBack={vi.fn()} onTaskClick={vi.fn()} />
        </ServiceContext.Provider>
      </MemoryRouter>
    )
  }

  it('renders loading state initially', () => {
    renderWithProviders('1')
    expect(screen.getByText('Loading diagnostic data...')).toBeInTheDocument()
  })

  it('renders spec title and summary after loading', async () => {
    renderWithProviders('1')
    await waitFor(() => {
      expect(screen.getByText('Test Spec')).toBeInTheDocument()
    })
  })

  it('renders timeline and dag sections', async () => {
    renderWithProviders('1')
    await waitFor(() => {
      expect(screen.getByText('Spec Journey')).toBeInTheDocument()
      expect(screen.getByText('Dependency Graph')).toBeInTheDocument()
    })
  })

  it('renders problems detected section', async () => {
    renderWithProviders('1')
    await waitFor(() => {
      expect(screen.getByText('Problems Detected')).toBeInTheDocument()
    })
  })

  it('shows DagView with correct task count', async () => {
    renderWithProviders('1')
    await waitFor(() => {
      expect(screen.getByTestId('dag-view')).toHaveTextContent('DagView (2 tasks)')
    })
  })
})
