// ─── Comment Taxonomy ───────────────────────────────────────

export type CommentType = 'status_update' | 'question' | 'reply' | 'proof' | 'summary' | 'reflection' | 'planning';

// ─── Dashboard Types ────────────────────────────────────────

export interface ModelInfo {
    name: string;
    description: string;
    is_default: boolean;
    supports_image_input?: boolean | null;
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
    referenceImages?: CommentFileAttachment[];
    usage?: {
        total_tokens?: number;
        input_tokens?: number;
        output_tokens?: number;
        cache_read_input_tokens?: number;
        cache_creation_input_tokens?: number;
    } | null;
    scheduleSummary?: {
        id: number;
        kind: 'ONE_TIME' | 'RECURRING';
        status: 'ACTIVE' | 'PAUSED' | 'CANCELED' | 'COMPLETED';
        timezone: string;
        next_run_at_utc?: string | null;
        materialized_task_id?: number | null;
        current_run_id?: number | null;
    } | null;
    scheduleRuns?: Array<{
        id: number;
        run_number: number;
        scheduled_for_utc: string;
        released_at_utc?: string | null;
        finished_at_utc?: string | null;
        status: string;
        terminal_task_status?: string | null;
        result_summary?: string;
    }>;
}

export interface Board {
    id: string;
    name: string;
    isTrial?: boolean;
    workingDir?: string | null;
    timezone?: string;
    odinInitialized?: boolean;
    memberIds: string[];
    agents?: AgentConfig[];
    tasks: Task[];
    members: Member[];
    lists: string[];
    totalActions: number;
    createdAt: string;
    taskCount?: number;
    memberCount?: number;
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
    fileName?: string;
    isManaged?: boolean;
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

export interface TaskSchedule {
    id: number;
    board_id: number;
    kind: 'ONE_TIME' | 'RECURRING';
    status: 'ACTIVE' | 'PAUSED' | 'CANCELED' | 'COMPLETED';
    timezone: string;
    starts_at_local: string;
    starts_at_utc: string;
    next_run_at_utc?: string | null;
    recurrence_rule?: Record<string, unknown>;
    materialized_task_id?: number | null;
    last_released_run_id?: number | null;
    paused_at?: string | null;
    canceled_at?: string | null;
    completed_at?: string | null;
    created_by: string;
    created_at: string;
    updated_at: string;
    template: {
        title: string;
        description?: string;
        priority?: string;
        assignee_id?: number | null;
        model_name?: string | null;
        label_ids?: number[];
        depends_on?: string[];
        dev_eta_seconds?: number | null;
        spec_id?: number | null;
        metadata?: Record<string, unknown>;
    };
    runs?: Array<{
        id: number;
        run_number: number;
        task_id?: number | null;
        scheduled_for_utc: string;
        released_at_utc?: string | null;
        finished_at_utc?: string | null;
        status: string;
        terminal_task_status?: string | null;
        result_summary?: string;
    }>;
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

export interface ForcedProviderStatus {
    enabled: boolean;
    provider: string | null;
    model: string | null;
    source: string;
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

export type ViewMode = 'overview' | 'board' | 'specs' | 'settings' | 'reflections' | 'notifications' | 'analytics' | 'scheduling';

// ─── Analytics Types ─────────────────────────────────────────

export interface AnalyticsSummaryKPIs {
    total_spend: number;
    total_tokens: number;
    task_count: number;
    avg_cost_per_task: number;
    reflection_cost: number;
}

export interface AnalyticsTimeSeries {
    date: string;
    total: number;
    by_model: Record<string, number>;
}

export interface AnalyticsCostByModel {
    model: string;
    cost: number;
    tokens: number;
    task_count: number;
}

export interface AnalyticsCostByBoard {
    board_id: number;
    board_name: string;
    cost: number;
    task_count: number;
}

export interface AnalyticsCostByAgent {
    agent: string;
    cost: number;
    tokens: number;
    task_count: number;
}

export interface AnalyticsEfficiencyMetrics {
    cache_hit_rate: number;
    failure_cost: number;
    avg_cost_per_task: number;
    reflection_cost: number;
    avg_tokens_per_task: number;
    avg_duration_ms: number;
    failed_task_count: number;
    total_task_count: number;
}

export interface AnalyticsModelComparison {
    model: string;
    avg_cost: number;
    total_cost: number;
    avg_duration_ms: number;
    avg_tokens: number;
    success_rate: number;
    task_count: number;
}

export interface AnalyticsTopExpensiveTask {
    task_id: number;
    title: string;
    model: string;
    status: string;
    cost: number;
    total_tokens: number;
}

export interface AnalyticsCostSummary {
    summary_kpis: AnalyticsSummaryKPIs;
    time_series: AnalyticsTimeSeries[];
    cost_by_model: AnalyticsCostByModel[];
    cost_by_board: AnalyticsCostByBoard[];
    cost_by_agent: AnalyticsCostByAgent[];
    efficiency_metrics: AnalyticsEfficiencyMetrics;
    model_comparison: AnalyticsModelComparison[];
    top_expensive_tasks: AnalyticsTopExpensiveTask[];
    meta: { task_count: number; granularity: string };
}

// ─── Notification Types ───────────────────────────────────────

export type NotificationType = 'task_assigned' | 'comment_added' | 'status_changed' | 'planning_complete' | 'question_asked' | 'spec_finished';

export interface Notification {
    id: number;
    recipient: number;
    notification_type: NotificationType;
    title: string;
    body: string;
    task: number | null;
    task_title: string | null;
    spec: number | null;
    spec_title: string | null;
    board: number | null;
    board_name: string | null;
    actor_email: string;
    is_read: boolean;
    created_at: string;
}

export interface NotificationPreference {
    desktop_enabled: boolean;
    sound_enabled: boolean;
    disabled_types: string[];
    quiet_hours_start: string | null;
    quiet_hours_end: string | null;
    quiet_hours_timezone: string;
}
