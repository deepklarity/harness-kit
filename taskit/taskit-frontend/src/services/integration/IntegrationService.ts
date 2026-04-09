import type {
    AgentConfig,
    Board,
    DashboardData,
    KanbanColumnsResponse,
    KanbanLoadMoreResponse,
    Member,
    MemberListQuery,
    PaginatedResponse,
    PresetsResponse,
    Spec,
    SpecCommit,
    SpecListQuery,
    Task,
    TaskListQuery,
    TimelineQuery,
    ReflectionReport,
    ReflectionRequest,
    OdinStatusResponse,
    ProcessMonitorResponse,
    ForcedProviderStatus,
    AnalyticsCostSummary,
    ProviderQuota,
    TaskSchedule,
    IdeOptions,
    IdeSettings,
    TaskIdeOptions,
} from '../../types';

export interface AuthState {
    isAuthenticated: boolean;
    apiKey: string | null;
    token: string | null;
}

export interface DirectoryEntry {
    name: string;
    path: string;
    has_children: boolean;
}

export interface DirectoryCheckResult {
    odin_exists: boolean;
    linked_board: { id: number; name: string } | null;
    can_init: boolean;
    message: string;
    resolved_path?: string;
}

export interface TaskSearchResult {
    taskId: string;
    title: string;
    status: string;
    boardId: string;
    boardName: string;
    specId?: string;
    specTitle?: string;
}

export interface IntegrationService {
    readonly name: string;

    getAuthState(): AuthState;
    login(apiKey: string): void;
    logout(): void;
    saveCredentials(apiKey: string, token: string): void;
    handleCallback(): boolean;

    fetchData(): Promise<DashboardData>;
    fetchTaskDetail(taskId: string): Promise<Task>;
    fetchTasksPage(query: TaskListQuery): Promise<PaginatedResponse<Task>>;
    fetchMembersPage(query: MemberListQuery): Promise<PaginatedResponse<Member>>;
    fetchSpecsPage(query: SpecListQuery): Promise<PaginatedResponse<Spec>>;
    fetchBoardsPage(query: { search?: string; sort?: string; page?: number; page_size?: number }): Promise<PaginatedResponse<Board>>;
    fetchTimelinePage(query: TimelineQuery): Promise<PaginatedResponse<Task>>;
    fetchKanban(boardId?: string, query?: { date_from?: string; date_to?: string }): Promise<KanbanColumnsResponse>;
    fetchKanbanMore(boardId: string, status: string, offset: number, limit: number, query?: { date_from?: string; date_to?: string }): Promise<KanbanLoadMoreResponse>;
    searchTasks(query: { q: string; scope: 'board' | 'global'; boardId?: string; limit?: number }): Promise<TaskSearchResult[]>;
    fetchSchedules(query?: { board?: string; status?: string[]; kind?: string[]; history?: boolean; q?: string; sort?: string; created_from?: string; created_to?: string; page?: number; page_size?: number }): Promise<PaginatedResponse<TaskSchedule>>;
    createSchedule(payload: Record<string, unknown>): Promise<TaskSchedule>;
    updateSchedule(scheduleId: string, payload: Record<string, unknown>): Promise<TaskSchedule>;
    pauseSchedule(scheduleId: string): Promise<TaskSchedule>;
    resumeSchedule(scheduleId: string): Promise<TaskSchedule>;
    cancelSchedule(scheduleId: string): Promise<TaskSchedule>;
    deleteSchedule(scheduleId: string): Promise<void>;
    suggestDirectories(query: string, limit?: number): Promise<DirectoryEntry[]>;
    listDirectoryChildren(path: string, limit?: number): Promise<DirectoryEntry[]>;

    checkDirectory(path: string, options?: { mode?: 'existing' | 'create'; parentDirectory?: string; directoryName?: string }): Promise<DirectoryCheckResult>;
    fetchBoardMembers(boardId: string): Promise<Member[]>;

    updateTaskAssignees(taskId: string, memberIds: string[]): Promise<void>;
    createBoard(input: {
        name: string;
        description?: string;
        disabledAgents?: string[];
    } & (
            { directoryMode: 'existing'; workingDir: string }
            | { directoryMode: 'create'; parentDirectory: string; directoryName: string }
        )): Promise<unknown>;
    deleteBoard(boardId: string): Promise<void>;
    initOdin(boardId: string): Promise<unknown>;
    updateBoard(boardId: string, updates: Record<string, unknown>): Promise<unknown>;
    createTask(
        boardId: string, title: string, description: string,
        priority?: string, createdBy?: string, devEta?: number,
        options?: {
            createdByUserId?: number;
            assigneeId?: number;
            modelName?: string;
            labelIds?: number[];
            dependsOn?: string[];
            workingDir?: string;
            skipReflection?: boolean;
        }
    ): Promise<unknown>;
    updateTask(taskId: string, updates: {
        title?: string; description?: string; priority?: string; devEta?: number; status?: string;
        labelIds?: number[]; modelName?: string; dependsOn?: string[];
        kanbanTargetIndex?: number; kanbanTargetStatus?: string; skipReflection?: boolean;
    }): Promise<void>;
    stopExecution(taskId: string, targetStatus: string): Promise<void>;
    stopRuntimeTask(taskId: string, targetStatus?: string): Promise<void>;
    fetchOdinStatus(params?: { spec?: string; agent?: string; status?: string }): Promise<OdinStatusResponse>;
    fetchForcedProviderStatus(): Promise<ForcedProviderStatus>;
    fetchProcessMonitor(params?: { boardId?: string; specId?: string; runningOnly?: boolean }): Promise<ProcessMonitorResponse>;
    fetchIdeOptions(): Promise<IdeOptions>;
    fetchIdeSettings(): Promise<IdeSettings>;
    saveIdeSettings(preferredIdeId: string | null): Promise<IdeSettings>;
    fetchTaskIdeOptions(taskId: string): Promise<TaskIdeOptions>;
    openTaskProject(taskId: string): Promise<void>;

    getAvailableStatuses(): string[];

    fetchSpecs?(): Promise<Spec[]>;
    fetchSpecDetail?(id: string): Promise<Spec>;
    fetchSpecCommits?(specId: string): Promise<SpecCommit[]>;
    finalizeSpec?(specId: string): Promise<{ pr_url?: string; finalized_at?: string; error?: string }>;


    uploadScreenshots(taskId: string, files: File[], authorEmail?: string): Promise<unknown>;


    // Comments
    addComment(taskId: string, content: string, authorEmail?: string, commentType?: string): Promise<void>;
    replyToQuestion(taskId: string, questionCommentId: string, content: string, authorEmail?: string): Promise<void>;

    // Reflections
    fetchReflections(taskId: string): Promise<ReflectionReport[]>;
    fetchAllReflections(params?: { status?: string; verdict?: string }): Promise<ReflectionReport[]>;
    fetchReflectionById(reportId: number): Promise<ReflectionReport>;
    triggerReflection(taskId: string, params: ReflectionRequest): Promise<ReflectionReport>;
    cancelReflection(reportId: number): Promise<ReflectionReport>;
    deleteReflection(reportId: number): Promise<void>;

    // Spec files (preexisting)
    fetchBoardSpecFiles(boardId: string): Promise<{ name: string; path: string }[]>;
    readBoardSpecFile(boardId: string, path: string): Promise<{ name: string; content: string }>;

    // Agents (config-based)
    fetchBoardAgents(boardId: string): Promise<AgentConfig[]>;
    toggleBoardAgent(boardId: string, agentName: string, enabled: boolean): Promise<{ name: string; enabled: boolean; board?: Record<string, unknown> }>;
    toggleBoardModel(boardId: string, agentName: string, modelName: string, enabled: boolean): Promise<void>;

    // Analytics
    fetchAnalytics(params?: { board?: string; date_from?: string; date_to?: string; granularity?: string }): Promise<AnalyticsCostSummary>;
    fetchQuotaStatus(): Promise<ProviderQuota[]>;

    // Presets
    fetchPresets(): Promise<PresetsResponse>;
}
