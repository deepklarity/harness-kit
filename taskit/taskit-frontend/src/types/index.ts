// ─── Comment Taxonomy ───────────────────────────────────────

export type CommentType = 'status_update' | 'question' | 'reply' | 'proof' | 'summary' | 'reflection' | 'planning';

// ─── Dashboard Types ────────────────────────────────────────

export interface ModelInfo {
    name: string;
    description: string;
    is_default: boolean;
    input_price_per_1m_tokens?: number | null;
    output_price_per_1m_tokens?: number | null;
    cache_read_price_per_1m_tokens?: number | null;
}

export interface Member {
    id: string;
    fullName: string;
    username: string;
    initials: string;
    avatarUrl: string | null;
    color: string;
    email: string;
    role: 'HUMAN' | 'AGENT' | 'ADMIN';
    taskCount: number;
    totalTimeSpentMs: number;
    availableModels: ModelInfo[];
    cost_tier?: string;
    capabilities?: string[];
}

export interface CommentFileAttachment {
    id: number;
    url: string;
    originalFilename: string;
    contentType: string;
    fileSize: number;
    uploadedBy: string;
    createdAt: string;
}

export interface TaskComment {
    id: string;
    taskId: string;
    authorEmail: string;
    authorLabel: string;
    content: string;
    attachments: unknown[];
    commentType: CommentType;
    createdAt: string;
    fileAttachments?: CommentFileAttachment[];
}

export interface TaskMutation {
    id: string;
    type: 'created' | 'status_change' | 'assigned' | 'description_update' | 'other';
    date: string;
    timestamp: number;
    actor: string;
    actorId: string;
    description: string;
    fromStatus?: string;
    toStatus?: string;
    assignedMember?: string;
    fieldName?: string;
    oldValue?: any;
    newValue?: any;
    cardDesc?: string;
}

export interface Label {
    id: number;
    name: string;
    color: string;
}

export interface Task {
    id: string;
    name: string;
    title?: string;
    idShort: number;
    shortLink: string;
    boardId: string;
    boardName: string;
    currentStatus: string;
    kanbanPosition?: number;
    assignees: string[];
    assigneeIds: string[];
    createdAt: string;
    createdBy: string;
    mutations: TaskMutation[];
    comments: TaskComment[];
    timeInStatuses: Record<string, number>;
    totalLifespanMs: number;
    workTimeMs: number;
    executingTimeMs: number;
    description?: string;
    devEta?: number;
    remainingTimeMs?: number;
    isTimerRunning?: boolean;
    priority?: string;
    specId?: string;
    specName?: string;
    labels?: Label[];
    // Odin execution context
    cwd?: string;
    complexity?: string;
    metadata?: Record<string, unknown>;
    dependsOn?: string[];
    modelName?: string;
    commentCount?: number;
    estimatedCostUsd?: number | null;
    reflectionCostUsd?: number | null;
    usage?: {
        total_tokens?: number;
        input_tokens?: number;
        output_tokens?: number;
        cache_read_input_tokens?: number;
        cache_creation_input_tokens?: number;
    } | null;
}

export interface Board {
    id: string;
    name: string;
    isTrial?: boolean;
    workingDir?: string | null;
    odinInitialized?: boolean;
    memberIds: string[];
    tasks: Task[];
    members: Member[];
    lists: string[];
    totalActions: number;
    createdAt: string;
}

export interface SpecCostSummary {
    total_cost_usd: number;
    reflection_cost_usd: number;
    cost_by_model: Record<string, number>;
    total_tokens: number;
    total_input_tokens: number;
    total_output_tokens: number;
    tokens_by_model: Record<string, number>;
    total_duration_ms: number;
    tasks_with_unknown_cost: number;
}

export interface SpecComment {
    id: string;
    specId: string;
    authorEmail: string;
    authorLabel: string;
    content: string;
    attachments: unknown[];
    commentType: CommentType;
    createdAt: string;
}

export interface Spec {
    id: string;

    title: string;
    source: string;
    content: string;
    abandoned: boolean;
    boardId: string;
    metadata: Record<string, unknown>;
    createdAt: string;
    tasks: Task[];
    taskCount: number;
    comments?: SpecComment[];
    cwd?: string;
    costSummary?: SpecCostSummary;
}

export interface DashboardData {
    boards: Board[];
    allTasks: Task[];
    allMembers: Member[];
    specs: Spec[];
    labels: Label[];
    generatedAt: string;
    stats: DashboardStats;
}

export interface DashboardStats {
    totalTasks: number;
    totalMembers: number;
    totalBoards: number;
    completedTasks: number;
    inProgressTasks: number;
    todoTasks: number;
    avgTimeToCompletionMs: number;
    totalMutations: number;
    mostActiveBoard: string;
    mostActiveMember: string;
}

export interface PaginatedResponse<T> {
    count: number;
    next: string | null;
    previous: string | null;
    results: T[];
}

export interface TaskListQuery {
    q?: string;
    status?: string[];
    assignee?: string[];
    priority?: string[];
    spec?: string[];
    labels?: string[];
    sort?: string;
    created_from?: string;
    created_to?: string;
    page?: number;
    page_size?: number;
    board?: string;
}

export interface MemberListQuery {
    q?: string;
    board?: string;
    role?: Array<'HUMAN' | 'AGENT' | 'ADMIN'>;
    joined_from?: string;
    joined_to?: string;
    sort?: string;
    page?: number;
    page_size?: number;
}

export interface SpecListQuery {
    q?: string;
    board?: string;
    status?: Array<'active' | 'abandoned'>;
    created_from?: string;
    created_to?: string;
    sort?: string;
    page?: number;
    page_size?: number;
}

export interface TimelineQuery {
    q?: string;
    status?: string[];
    assignee?: string[];
    priority?: string[];
    date_from?: string;
    date_to?: string;
    sort?: string;
    page?: number;
    page_size?: number;
    board?: string;
    view?: string;
}

export interface ProcessMonitorTask {
    task_id: number;
    title: string;
    status: string;
    odin_status: string;
    board_id: number;
    spec_id?: number | null;
    assignee?: string | null;
    agent?: string | null;
    model?: string | null;
    elapsed?: string | null;
    updated_at: string;
}

export interface ProcessMonitorResponse {
    tasks: ProcessMonitorTask[];
    summary: Record<string, number>;
    source: string;
    fetched_at: string;
}

export interface OdinStatusRow {
    id: string;
    title: string;
    status: string;
    agent: string;
    spec: string;
    model: string;
    deps: string;
    elapsed: string;
    updated_hms: string;
}

export interface OdinStatusResponse {
    ok: boolean;
    command: string[];
    exit_code: number | null;
    raw_stdout: string;
    raw_stderr: string;
    rows: OdinStatusRow[];
    summary: Record<string, number>;
    total: number;
    parse_ok: boolean;
    parse_warnings: string[];
    error: string;
    fetched_at: string;
}

// ─── Reflection Types ──────────────────────────────────────

export interface ReflectionReport {
    id: number;
    task: number;
    reviewer_agent: string;
    reviewer_model: string;
    custom_prompt: string;
    context_selections: string[];
    requested_by: string;
    status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED';
    quality_assessment: string;
    slop_detection: string;
    improvements: string;
    agent_optimization: string;
    verdict: string;
    verdict_summary: string;
    raw_output: string;
    execution_trace: string;
    assembled_prompt: string;
    duration_ms: number | null;
    token_usage: Record<string, unknown>;
    error_message: string;
    created_at: string;
    completed_at: string | null;
    task_title: string;
    estimated_cost_usd?: number | null;
}

export interface ReflectionRequest {
    reviewer_agent: string;
    reviewer_model: string;
    custom_prompt?: string;
    context_selections?: string[];
}

export interface AgentModelInfo {
    name: string;
    enabled: boolean;
    is_default: boolean;
    description: string;
}

export interface AgentConfig {
    name: string;
    enabled: boolean;
    cli_command?: string;
    capabilities: string[];
    cost_tier: 'low' | 'medium' | 'high';
    default_model?: string;
    premium_model?: string;
    models: AgentModelInfo[];
}

// ─── Preset Types ─────────────────────────────────────────

export interface PresetCategory {
    slug: string;
    name: string;
    description: string;
    icon: string;
    sort_order: number;
}

export interface TaskPreset {
    id: string;
    title: string;
    description: string;
    category: string;
    icon: string;
    suggested_priority: string;
    source: string;
    sort_order: number;
}

export interface PresetsResponse {
    version: number;
    categories: PresetCategory[];
    presets: TaskPreset[];
}

export type ViewMode = 'overview' | 'board' | 'specs' | 'settings' | 'reflections';
