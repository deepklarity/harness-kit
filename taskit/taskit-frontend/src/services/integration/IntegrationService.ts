import type {
    AgentConfig,
    Board,
    DashboardData,
    Member,
    MemberListQuery,
    PaginatedResponse,
    PresetsResponse,
    Spec,
    SpecListQuery,
    Task,
    TaskListQuery,
    TimelineQuery,
    ReflectionReport,
    ReflectionRequest,
    OdinStatusResponse,
    ProcessMonitorResponse,
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
    fetchKanban(boardId?: string, query?: { date_from?: string; date_to?: string }): Promise<Task[]>;
    suggestDirectories(query: string, limit?: number): Promise<DirectoryEntry[]>;
    listDirectoryChildren(path: string, limit?: number): Promise<DirectoryEntry[]>;
    checkDirectory(path: string): Promise<DirectoryCheckResult>;

    updateTaskAssignees(taskId: string, memberIds: string[]): Promise<void>;
    createBoard(name: string, description?: string, workingDir?: string, disabledAgents?: string[]): Promise<unknown>;
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
            workingDir?: string;
        }
    ): Promise<unknown>;
    updateTask(taskId: string, updates: {
        title?: string; description?: string; priority?: string; devEta?: number; status?: string;
        kanbanTargetIndex?: number; kanbanTargetStatus?: string;
    }): Promise<void>;
    stopExecution(taskId: string, targetStatus: string): Promise<void>;
    stopRuntimeTask(taskId: string, targetStatus?: string): Promise<void>;
    fetchOdinStatus(params?: { spec?: string; agent?: string; status?: string }): Promise<OdinStatusResponse>;
    fetchProcessMonitor(params?: { boardId?: string; specId?: string; runningOnly?: boolean }): Promise<ProcessMonitorResponse>;

    getAvailableStatuses(): string[];

    fetchSpecs?(): Promise<Spec[]>;
    fetchSpecDetail?(id: string): Promise<Spec>;

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

    // Agents (config-based)
    fetchBoardAgents(boardId: string): Promise<AgentConfig[]>;
    toggleBoardAgent(boardId: string, agentName: string, enabled: boolean): Promise<{ name: string; enabled: boolean; board?: Record<string, unknown> }>;
    toggleBoardModel(boardId: string, agentName: string, modelName: string, enabled: boolean): Promise<void>;

    // Presets
    fetchPresets(): Promise<PresetsResponse>;
}
