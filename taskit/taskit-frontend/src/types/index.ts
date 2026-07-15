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
    skipReflection?: boolean;
    boardSkipReflection?: boolean;
    boardSkipProof?: boolean;
    boardEscalationEnabled?: boolean;
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
    needsHuman?: boolean;
    needsHumanReason?: string;
    // Task #359 — the failure banner must always describe the failure
    // that parked the task, with an honest one-line next step. The
    // serializer composes both from the failure_class + reason; the
    // banner renders them verbatim, no recomputation on the client.
    failureSuggestedAction?: string;
    failureHumanReason?: string;
    // Memory: closest finished twins + quote (exposed on the detail API).
    twins?: TaskTwin[];
    estimate?: TaskEstimate | null;
    actual?: TaskActual | null;
}

export interface TaskTwin {
    task_id: number;
    title: string;
    outcome: string;
    tokens: number | null;
    duration_ms: number | null;
    redo_rounds: number;
    agent: string | null;
    model: string | null;
    score: number;
    text_score: number;
    structural_score: number;
    proof_path: string;
    warning?: { failure_class?: string | null; one_liner: string } | null;
}

export interface TaskAssignmentReasonCheaperAlternative {
    agent: string;
    model: string | null;
    tier?: string | null;
    success_rate: number | null;
    median_tokens?: number | null;
    reason: string;
}

export interface TaskAssignmentReasonTwinConsensus {
    agent: string;
    landed: number;
    total: number;
}

// Surfacing the WHY behind agent assignment (W6.14).
// Stamped at dispatch by odin and stamped-with-override=true by the
// taskit-backend when a human reassigns the task after dispatch.
export interface TaskAssignmentReason {
    agent: string;
    model: string | null;
    rule: 'suggested-agent' | 'suggested-model' | 'history' | 'static-fallback' | 'escalated';
    reason: string;
    override: boolean;
    override_by?: string | null;
    cheaper_alternatives: TaskAssignmentReasonCheaperAlternative[];
    twin_consensus: TaskAssignmentReasonTwinConsensus | null;
}

export interface TaskEstimate {
    confidence: string;
    twin_count: number;
    tokens_median?: number | null;
    duration_ms_median?: number | null;
    source_twin_ids?: number[];
}

export interface TaskActual {
    tokens: number | null;
    duration_ms: number | null;
    transition: string;
}

export interface Board {
    id: string;
    name: string;
    isTrial?: boolean;
    workingDir?: string | null;
    timezone?: string;
    odinInitialized?: boolean;
    claudeTokenConfigured?: boolean;  // whether a .claude-token exists in working_dir (write-only)
    skipReflection?: boolean;
    skipProof?: boolean;
    autoStartPlannedTasks?: boolean;
    reflectionModel?: string | null;
    // Size-bucketed reviewer selection: { small?, medium?, large? } → model name.
    // Empty/absent preserves the single-reviewer default (reflectionModel).
    reflectionReviewStrategy?: Record<string, string> | null;
    modelEscalationPriority?: EscalationPriorityEntry[];
    reviewerOrder?: EscalationPriorityEntry[];
    escalationEnabled?: boolean;
    failureMaxRetries?: number;
    // task #328: board-level overrides for routing (peer preference order,
    // capability-escalation threshold, per-failure-class action tuning).
    // {} means "use built-in defaults" — see routing-config for the merged,
    // effective view used for display.
    routingPolicy?: Record<string, unknown>;
    // Opt-in: if false (default), tasks with no spec cannot be dispatched
    // (moved to IN_PROGRESS) — there is no worktree to execute in.
    allowProjectRootExecution?: boolean;
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
    plan_cost_usd: number;
    total_cost_usd: number;
    reflection_cost_usd: number;
    merge_cost_usd: number;
    cost_by_model: Record<string, number>;
    total_tokens: number;
    total_input_tokens: number;
    total_output_tokens: number;
    tokens_by_model: Record<string, number>;
    total_duration_ms: number;
    tasks_with_unknown_cost: number;
}

export interface SpecMergeSummary {
    attempt_count: number;
    static_count: number;
    agent_count: number;
    human_assisted_count: number;
    merge_cost_usd: number;
    mean_dispatch_lag_seconds: number | null;
    conflicts_by_file: Record<string, number>;
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

export interface PlannerConfig {
    agent?: string;
    model?: string;
    quick?: boolean;
    auto?: boolean;
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
    mergeSummary?: SpecMergeSummary;
    status?: 'planning' | 'planning_complete' | 'planning_failed' | 'active';
    plannerConfig?: PlannerConfig;
    fileName?: string;
    isManaged?: boolean;
}

export interface SpecCommit {
    hash: string;
    short_hash: string;
    message: string;
    author: string;
    date: string;
}

// Passthrough shape from GET /specs/:id/story/ — field names kept snake_case
// verbatim, matching SpecCostSummary's convention for backend-computed data.
export interface SpecStoryRedoRound {
    id: number;
    verdict: string | null;
    reviewer_agent: string;
    reviewer_model: string;
    created_at: string;
}

export interface SpecStoryMerge {
    status: string;
    mode: string;
    auto_resolved: boolean;
    conflicting_files: string[];
    error: string;
    diff_stat: string;
}

export interface SpecStoryComment {
    id: number;
    comment_type: string;
    author: string;
    created_at: string;
    headline: string;
}

export interface SpecStoryTask {
    task_id: number;
    title: string;
    status: string;
    agent: string | null;
    model: string | null;
    dispatched_at: string | null;
    duration_ms: number | null;
    tokens: { total: number; input: number; output: number };
    cost_usd: number | null;
    redo_rounds: { count: number; verdicts: SpecStoryRedoRound[] };
    merge: SpecStoryMerge | null;
    latest_comment: SpecStoryComment | null;
    depends_on: string[];
    gaps: string[];
}

export interface SpecStory {
    spec_id: number;
    odin_id: string;
    title: string;
    task_count: number;
    tasks: SpecStoryTask[];
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

export interface DetectedIde {
    id: string;
    label: string;
    icon_key: string;
}

export interface IdeSettings {
    preferred_ide_id: string | null;
}

export interface IdeOptions {
    preferred_ide_id: string | null;
    detected_ides: DetectedIde[];
}

export interface TaskIdeOptions {
    project_root: string | null;
    preferred_ide_id: string | null;
    detected_ides: DetectedIde[];
    has_configured_ide: boolean;
}

// ─── Executor / Sandbox Capacity Types ─────────────────────

export interface MemoryShareHolder {
    task_id: string;
    task_title: string;
    kind: 'execution' | 'reflection';
    mem_mib: number;
    report_id?: number;
}

export interface MemorySharesBlock {
    budget_mib: number | null;
    reserved_mib: number;
    default_vm_mem_mib: number;
    max_shares: number;
    executing_count: number;
    reflecting_count: number;
    shares_in_use: number;
    holders: MemoryShareHolder[];
}

export interface ExecutorCapacity {
    running: number;
    max: number;
    suggested_max: number;
    // Task #353: split the running count by VM kind and surface the
    // holders so the badge can render "3+1/4" with a tooltip naming each
    // share-holder — same accounting the dispatcher uses, no parallel
    // surface.
    executing?: number;
    reflecting?: number;
    shares_in_use?: number;
    memory_budget_mib?: number | null;
    memory_reserved_mib?: number;
    memory_max_shares?: number;
    memory_share_holders?: MemoryShareHolder[];
}

export interface ExecutorMaxConcurrency {
    value: number;
    suggested_max: number;
}

// ─── Kanban Types ───────────────────────────────────────────

export interface KanbanColumnsResponse {
    columns: Record<string, { tasks: Task[]; totalCount: number }>;
}

export interface KanbanLoadMoreResponse {
    tasks: Task[];
    totalCount: number;
    hasMore: boolean;
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

export interface EscalationHistoryEntry {
    from_model: string | null;
    from_agent: string | null;
    to_model: string;
    to_agent: string;
}

export interface EscalationPriorityEntry {
    agent_name: string;
    model_name: string;
}

// task #328: the EFFECTIVE routing policy (built-in defaults + board
// overrides already merged), returned by GET /api/boards/{id}/routing-config/
// for read/display in the Settings "Routing" section.
export interface RoutingFailureAction {
    action: 'auto_requeue' | 'reassign' | 'human';
    max_retries: number;
    backoff_seconds: number;
    peer_fallback: boolean;
    description: string;
}

export interface RoutingConfig {
    preference_order: string[];
    default_preference_order: string[];
    capability_escalate_after: number;
    // task #328: the capability tier-jump is governed by the ONE policy
    // engine, resolved from routing_policy (Default First → legacy fields).
    capability_escalation_enabled: boolean;
    capability_max_escalations: number;
    escalation_enabled: boolean;
    escalation_tiers: EscalationPriorityEntry[];
    failure_actions: Record<string, RoutingFailureAction>;
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
    disabled?: boolean;
}

export interface PresetsResponse {
    version: number;
    categories: PresetCategory[];
    presets: TaskPreset[];
}

export type ViewMode = 'overview' | 'board' | 'specs' | 'settings' | 'reflections' | 'notifications' | 'analytics' | 'scheduling'| 'providers' | 'factory' | 'league';

// ─── Factory Types ────────────────────────────────────────────

export interface FactoryRunningTask {
    task_id: string;
    task_title: string;
    run_token: string;
    state: string;
    started_at: string;
    last_heartbeat: string;
    seconds_since_heartbeat: number;
}

export interface FactoryQueues {
    waiting: number;
    executing: number;
    review: number;
    shelf: number;
}

export interface FactoryMergeAttempt {
    id: number;
    task_id: string;
    task_title: string;
    mode: string;
    outcome: string;
    trigger: string;
    started_at: string;
    finished_at: string;
    lag_seconds: number | null;
}

export interface FactoryOpenError {
    source: string;
    signature: string;
    latest_symptom: string;
    latest_disposition: string;
    count: number;
    event_ids: number[];
    latest_at: string;
}

export interface FactoryStory {
    headline: string | null;
    tldr: {
        landed: number;
        hands_free: number;
        incidents: number;
        waiting_on_human: number;
    };
    event_count: number;
}

export interface FactorySnapshot {
    board_id: string;
    board_name: string;
    running: FactoryRunningTask[];
    queues: FactoryQueues;
    memory_shares?: MemorySharesBlock;
    recent_merges: FactoryMergeAttempt[];
    open_errors: FactoryOpenError[];
    story: FactoryStory;
}

// ─── Inbox Types (everything waiting on a human) ──────────────

export interface InboxParkedMerge {
    task_id: string;
    task_title: string;
    why: string;
    question_comment_id: string | null;
    conflicting_files: string[];
}

export interface InboxReversibilityPark {
    task_id: string;
    task_title: string;
    why: string;
    question_comment_id: string | null;
    action_key: string;
    branch: string;
}

export interface InboxShelfTask {
    task_id: string;
    task_title: string;
}

export interface InboxOpenError {
    source: string;
    signature: string;
    latest_symptom: string;
    count: number;
    event_ids: number[];
    latest_at: string;
    task_id: string | null;
}

export interface InboxFailedTask {
    task_id: string;
    task_title: string;
    last_failure_reason: string;
    failure_class: string;
    failed_at: string;
    blocked_count: number;
}

export interface InboxSnapshot {
    board_id: string;
    board_name: string;
    parked_merges: InboxParkedMerge[];
    reversibility_parks: InboxReversibilityPark[];
    testing_shelf: InboxShelfTask[];
    open_errors: InboxOpenError[];
    failed_tasks: InboxFailedTask[];
    counts: {
        parked_merges: number;
        reversibility_parks: number;
        testing_shelf: number;
        open_errors: number;
        failed_tasks: number;
    };
}

// ─── Analytics Types ─────────────────────────────────────────

export interface AnalyticsSummaryKPIs {
    total_spend: number;
    total_tokens: number;
    total_input_tokens: number;
    total_output_tokens: number;
    total_cache_read_tokens: number;
    task_count: number;
    avg_cost_per_task: number;
    reflection_cost: number;
    plan_cost: number;
    merge_cost: number;
}

export interface AnalyticsTimeSeries {
    date: string;
    total: number;
    by_model: Record<string, number>;
    // Landed (DONE) task count for this bucket — a separate bucketing
    // dimension from `total` (see backend _aggregate_time_series).
    task_count: number;
}

export interface AnalyticsCostByModel {
    model: string;
    cost: number;
    tokens: number;
    input_tokens: number;
    output_tokens: number;
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
    avg_input_tokens: number;
    avg_output_tokens: number;
    success_rate: number;
    task_count: number;
    reflection_pass_rate: number | null;
}

export interface AnalyticsTopExpensiveTask {
    task_id: number;
    title: string;
    model: string;
    status: string;
    cost: number;
    total_tokens: number;
}

export interface AnalyticsAutonomy {
    total_done: number;
    agent_authored: number;
    autonomy_rate: number;
    operator_touches_total: number;
    tasks_with_capture_gaps: number;
    exec_duration_seconds: { min: number; max: number; p50: number; p90: number };
    dispatch_to_done_seconds: { min: number; max: number; p50: number; p90: number };
}

export interface AnalyticsFailureBucket {
    class: string;
    count: number;
}

export interface AnalyticsFailureClassBreakdown {
    buckets: AnalyticsFailureBucket[];
    total_failed: number;
}

export interface AnalyticsReworkBucket {
    rounds: string;
    tasks: number;
}

export interface AnalyticsPerAgentRollup {
    agent: string;
    cost: number;
    tokens: number;
    tasks: number;
    rework_rounds: number;
    rework_tasks: number;
    agent_authored: number;
}

export interface AnalyticsMergeModeBucket {
    mode: string;
    count: number;
}

export interface AnalyticsMergeOutcomeBucket {
    outcome: string;
    count: number;
}

export interface AnalyticsMergeHealth {
    total_attempts: number;
    by_mode: AnalyticsMergeModeBucket[];
    by_outcome: AnalyticsMergeOutcomeBucket[];
    lag_seconds: { min: number; max: number; p50: number; p90: number };
}

export interface AnalyticsVerdictBucket {
    verdict: string;
    count: number;
}

export interface AnalyticsReviewHealth {
    total_reviews: number;
    by_verdict: AnalyticsVerdictBucket[];
}

export interface AnalyticsFunnelBucket {
    bucket: 'pass' | 'rework' | 'fail' | 'in_flight';
    count: number;
    pct: number;
}

export interface AnalyticsThroughputFunnel {
    total: number;
    buckets: AnalyticsFunnelBucket[];
}

export interface AnalyticsLeagueSection {
    rows: LeagueRow[];
    meta: {
        task_count: number;
        board_id: number | null;
        since_spec: string | null;
        aggregate: boolean;
    };
}

export interface AnalyticsPerSpecRow {
    odin_id: string;
    title: string;
    board_id: number | null;
    board_name: string | null;
    task_count: number;
    done_count: number;
    failed_count: number;
    in_flight_count: number;
    total_cost_usd: number;
    total_tokens: number;
    created_at: string | null;
}

export interface AnalyticsScheduledTaskRow {
    id: number;
    template_title: string;
    template_kind: string;
    status: string;
    board_id: number | null;
    board_name: string | null;
    run_count: number;
    success_count: number;
    failure_count: number;
    next_run_at_utc: string | null;
    created_at: string | null;
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
    autonomy: AnalyticsAutonomy;
    failure_class_breakdown: AnalyticsFailureClassBreakdown;
    rework_breakdown: AnalyticsReworkBucket[];
    per_agent_rollup: AnalyticsPerAgentRollup[];
    merge_health: AnalyticsMergeHealth;
    review_health: AnalyticsReviewHealth;
    throughput_funnel: AnalyticsThroughputFunnel;
    league: AnalyticsLeagueSection;
    per_spec: AnalyticsPerSpecRow[];
    scheduled_tasks: AnalyticsScheduledTaskRow[];
    meta: { task_count: number; granularity: string };
}

export interface ProviderQuota {
    provider: string;
    plan: string | null;
    usage_pct: number | null;
    used: number | null;
    limit: number | null;
    remaining: number | null;
    unit: string;
    reset_date: string | null;
    state: string | null;
    error: string | null;
    last_fetched: string | null;
    raw: Record<string, unknown> | null;
}

// ─── Notification Types ───────────────────────────────────────

export type NotificationType = 'task_assigned' | 'comment_added' | 'status_changed' | 'planning_complete' | 'question_asked' | 'spec_finished' | 'task_failed' | 'task_failed_reminder';

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

// ─── Provider Usage Types ────────────────────────────────────

export interface ProviderUsage {
    name: string;
    state: 'online' | 'offline' | 'degraded' | 'unknown';
    latency_ms: number | null;
    last_checked: string | null;
    message: string | null;
    plan: string | null;
    quota_limit: number | null;
    used: number | null;
    remaining: number | null;
    usage_pct: number | null;
    unit: string;
    reset_date: string | null;
    raw: Record<string, unknown> | null;
}

export interface ProviderUsageResponse {
    providers: ProviderUsage[];
    error?: string;
    fetched_at: string;
}

export interface LeagueRow {
    agent: string;
    model: string;
    tasks_landed: number;
    hands_free_count: number;
    hands_free_pct: number;
    redo_rounds_avg: number;
    tokens_median: number;
    duration_ms_median: number;
    merge_conflicts_caused: number;
    cost_usd_total: number;
    reflection_count: number;
    reflection_cost_usd_total: number;
    avg_reflection_cost_usd: number;
}

export interface LeagueResponse {
    rows: LeagueRow[];
    meta: {
        board_id: number;
        task_count: number;
        since_spec: string | null;
    };
}

export interface KanbanColumnsResponse {
    columns: Record<string, { tasks: Task[]; totalCount: number }>;
}

export interface KanbanLoadMoreResponse {
    tasks: Task[];
    totalCount: number;
    hasMore: boolean;
}
