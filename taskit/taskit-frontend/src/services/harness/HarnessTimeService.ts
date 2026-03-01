import type { IntegrationService, AuthState, DirectoryEntry } from '../integration/IntegrationService';
import type {
    AgentConfig,
    Board as DashBoard,
    DashboardData,
    DashboardStats,
    Label,
    Member,
    MemberListQuery,
    PaginatedResponse,
    PresetsResponse,
    Spec,
    SpecComment,
    SpecListQuery,
    Task as DashTask,
    TaskComment,
    TaskListQuery,
    TaskMutation,
    TimelineQuery,
    CommentType,
    OdinStatusResponse,
    ReflectionReport,
    ReflectionRequest,
    ProcessMonitorResponse,
} from '../../types';

export interface ParsedActor {
    agent?: string;
    model?: string;
    display: string;
}

export function parseActor(email: string): ParsedActor {
    if (email.endsWith('@odin.agent')) {
        const local = email.split('@')[0];
        const plusIdx = local.indexOf('+');
        if (plusIdx !== -1) {
            const agent = local.substring(0, plusIdx);
            const model = local.substring(plusIdx + 1);
            return { agent, model, display: `${agent} (${model})` };
        }
        return { agent: local, display: local };
    }
    if (email === 'odin@harness.kit') {
        return { agent: 'odin', display: 'odin' };
    }
    return { display: email };
}

interface HarnessBoard {
    id: number;
    name: string;
    description?: string;
    is_trial?: boolean;
    working_dir?: string | null;
    odin_initialized?: boolean;
    member_ids?: number[];
    tasks?: HarnessTask[];
    created_at?: string;
    updated_at?: string;
    member_count?: number;
}

interface HarnessUser {
    id: number;
    name: string;
    email: string;
    created_at: string;
    color?: string;
    role?: 'HUMAN' | 'AGENT' | 'ADMIN';
    available_models?: Array<{name: string; description: string; is_default: boolean; input_price_per_1m_tokens?: number | null; output_price_per_1m_tokens?: number | null; cache_read_price_per_1m_tokens?: number | null}>;
    cost_tier?: string;
    capabilities?: string[];
}

interface HarnessTask {
    id: number;
    board_id: number;
    title: string;
    description: string;
    priority: string;
    status: string;
    kanban_position?: number;
    assignee?: HarnessUser;
    assignee_id?: number;
    created_at: string;
    created_by?: string;
    last_updated_at: string;
    dev_eta_seconds?: number;
    labels?: Label[];
    spec_id?: number;
    // Odin fields
    complexity?: string;
    metadata?: Record<string, unknown>;
    result?: string;
    depends_on?: string[];
    model_name?: string;
    estimated_cost_usd?: number | null;
    reflection_cost_usd?: number | null;
    usage?: {
        total_tokens?: number;
        input_tokens?: number;
        output_tokens?: number;
        cache_read_input_tokens?: number;
        cache_creation_input_tokens?: number;
    } | null;
    time_in_statuses?: Record<string, number>;
    history?: HarnessTaskHistory[];
    comments?: HarnessTaskComment[];
    comment_count?: number;
}

interface HarnessTaskComment {
    id: number;
    task_id: number;
    author_email: string;
    author_label: string;
    content: string;
    attachments: unknown[];
    comment_type?: string;
    created_at: string;
    file_attachments?: Array<Record<string, unknown>>;
}

interface HarnessTaskHistory {
    id: number;
    task_id: number;
    field_name: string;
    old_value: string;
    new_value: string;
    changed_at: string;
    changed_by?: string;
}

interface HarnessSpecComment {
    id: number;
    spec_id: number;
    author_email: string;
    author_label: string;
    content: string;
    attachments: unknown[];
    comment_type?: string;
    created_at: string;
}

interface HarnessSpec {
    id: number;
    odin_id: string;
    title: string;
    source: string;
    content: string;
    abandoned: boolean;
    board_id: number;
    metadata: Record<string, unknown>;
    created_at: string;
    tasks?: HarnessTask[];
    task_count?: number;
    comments?: HarnessSpecComment[];
    cost_summary?: {
        total_cost_usd: number;
        reflection_cost_usd: number;
        cost_by_model: Record<string, number>;
        total_tokens: number;
        total_input_tokens: number;
        total_output_tokens: number;
        tokens_by_model: Record<string, number>;
        total_duration_ms: number;
        tasks_with_unknown_cost: number;
    };
}

interface HarnessTaskDetail extends HarnessTask {
    spec_title?: string;
}

interface PaginatedResponseBody<T> {
    count: number;
    next: string | null;
    previous: string | null;
    results: T[];
}

interface DirectoryEntriesResponse {
    base_path: string;
    entries: DirectoryEntry[];
}

export class ApiError extends Error {
    readonly status: number;
    readonly statusText: string;
    readonly body: unknown;

    constructor(status: number, statusText: string, body?: unknown) {
        super(`API Error ${status}: ${statusText}`);
        this.name = 'ApiError';
        this.status = status;
        this.statusText = statusText;
        this.body = body;
    }

    get isNotFound(): boolean { return this.status === 404; }
    get isUnauthorized(): boolean { return this.status === 401; }
    get isServerError(): boolean { return this.status >= 500; }
}

/** Infer comment type from attachments JSON — backward compat for pre-migration data. */
export function inferTypeFromAttachments(comment: { attachments?: unknown[]; content?: string }): CommentType {
    const attachments = comment.attachments || [];
    for (const att of attachments) {
        if (typeof att === 'object' && att !== null) {
            const a = att as Record<string, unknown>;
            if (a.type === 'question') return 'question';
            if (a.type === 'reply') return 'reply';
            if (a.type === 'proof') return 'proof';
            if (a.type === 'reflection') return 'reflection';
        }
    }
    return 'status_update';
}

export const MEMBER_COLORS = [
    '#6366f1', '#8b5cf6', '#ec4899', '#f43f5e',
    '#f97316', '#eab308', '#22c55e', '#14b8a6',
    '#06b6d4', '#3b82f6', '#a855f7', '#e11d48',
];

export class HarnessTimeService implements IntegrationService {
    readonly name = "Harness Time";
    private baseUrl: string;
    private tokenProvider: (() => Promise<string | null>) | null = null;
    private cachedLabels: Label[] = [];
    private cachedMembers: Map<string, Member> = new Map();
    private cachedSpecTitles: Map<string, string> = new Map();
    private cachedBoardNames: Map<string, string> = new Map();

    constructor() {
        this.baseUrl = import.meta.env.VITE_HARNESS_TIME_API_URL || 'http://localhost:8000';
    }

    setTokenProvider(provider: () => Promise<string | null>): void {
        this.tokenProvider = provider;
    }

    getAuthState(): AuthState {
        return { isAuthenticated: true, apiKey: null, token: null };
    }

    login(_apiKey: string): void {
        void _apiKey;
        /* handled by AuthContext */
    }
    logout(): void { /* handled by AuthContext */ }
    saveCredentials(_apiKey: string, _token: string): void {
        void _apiKey;
        void _token;
        /* handled by AuthContext */
    }
    handleCallback(): boolean { return false; }

    getCachedLabels(): Label[] {
        return this.cachedLabels;
    }

    private extractList<T>(payload: PaginatedResponseBody<T> | T[]): T[] {
        return Array.isArray(payload) ? payload : payload.results;
    }

    async fetchData(): Promise<DashboardData> {
        const [usersPage, boardsPage, specsPage, tasksPage, labelsPayload] = await Promise.all([
            this.get<PaginatedResponseBody<HarnessUser>>('/api/members/?page_size=200'),
            this.get<PaginatedResponseBody<HarnessBoard>>('/api/boards/?page_size=200'),
            this.get<PaginatedResponseBody<HarnessSpec>>('/api/specs/?page_size=500'),
            this.get<PaginatedResponseBody<HarnessTask>>('/api/tasks/?page_size=500'),
            this.get<PaginatedResponseBody<Label> | Label[]>('/api/labels/?page_size=500'),
        ]);

        const users = usersPage.results;
        const boards = boardsPage.results;
        const rawSpecs = specsPage.results;
        const allRawTasks = tasksPage.results;
        const labels = this.extractList(labelsPayload);
        this.cachedLabels = labels;

        const specMap = new Map<string, string>();
        rawSpecs.forEach(s => specMap.set(String(s.id), s.title));
        this.cachedSpecTitles = specMap;

        const membersMap = new Map<string, Member>();
        users.forEach((u, index) => {
            const models = u.available_models || [];
            membersMap.set(String(u.id), {
                id: String(u.id),
                fullName: u.name,
                username: u.email.split('@')[0],
                initials: u.name.substring(0, 2).toUpperCase(),
                avatarUrl: null,
                color: u.color || MEMBER_COLORS[index % MEMBER_COLORS.length],
                email: u.email,
                role: u.role || 'HUMAN',
                taskCount: 0,
                totalTimeSpentMs: 0,
                availableModels: models,
                cost_tier: u.cost_tier,
                capabilities: u.capabilities,
            });
        });

        this.cachedMembers = membersMap;

        // History comes inline with each task — no separate fetches needed
        const tasksByBoard = new Map<number, HarnessTask[]>();
        for (const t of allRawTasks) {
            const arr = tasksByBoard.get(t.board_id) || [];
            arr.push(t);
            tasksByBoard.set(t.board_id, arr);
        }

        const allTasks: DashTask[] = [];
        const transformedBoards: DashBoard[] = [];

        for (const b of boards) {
            this.cachedBoardNames.set(String(b.id), b.name);
            const boardTasks = tasksByBoard.get(b.id) || [];
            const transformedTasks: DashTask[] = [];

            for (const task of boardTasks) {
                const history = task.history || [];
                const mutations = this.transformHistory(history, membersMap);
                const timeInStatuses = this.computeTimeInStatuses(mutations, task.created_at);
                const totalLifespanMs = Date.now() - new Date(task.created_at).getTime();
                const workTimeMs = (timeInStatuses['IN_PROGRESS'] || 0) + (timeInStatuses['REVIEW'] || 0);
                const executingTimeMs = timeInStatuses['EXECUTING'] || 0;

                const assigneeIds = task.assignee ? [String(task.assignee.id)] : [];
                const assigneeNames = task.assignee ? [task.assignee.name] : [];

                const devEtaHours = task.dev_eta_seconds ? task.dev_eta_seconds / 3600 : undefined;
                let remainingTimeMs: number | undefined;
                if (devEtaHours !== undefined) {
                    const budgetedMs = devEtaHours * 3600 * 1000;
                    const spentMs = timeInStatuses['IN_PROGRESS'] || 0;
                    remainingTimeMs = budgetedMs - spentMs;
                }

                // Dashboard tasks have no comments (fetched on demand via detail endpoint)
                const comments: TaskComment[] = [];

                const dashTask: DashTask = {
                    id: String(task.id),
                    name: task.title || task.description.substring(0, 50),
                    title: task.title,
                    idShort: task.id,
                    shortLink: String(task.id),
                    boardId: String(b.id),
                    boardName: b.name,
                    currentStatus: task.status,
                    kanbanPosition: task.kanban_position,
                    assignees: assigneeNames,
                    assigneeIds: assigneeIds,
                    createdAt: task.created_at,
                    createdBy: task.created_by || 'Unknown',
                    mutations,
                    comments,
                    timeInStatuses,
                    totalLifespanMs,
                    workTimeMs,
                    executingTimeMs,
                    description: task.description,
                    devEta: devEtaHours,
                    remainingTimeMs,
                    isTimerRunning: task.status === 'IN_PROGRESS',
                    priority: task.priority,
                    specId: task.spec_id ? String(task.spec_id) : undefined,
                    specName: task.spec_id ? specMap.get(String(task.spec_id)) : undefined,
                    labels: task.labels || [],
                    complexity: task.complexity || undefined,
                    metadata: task.metadata && Object.keys(task.metadata).length > 0 ? task.metadata : undefined,
                    dependsOn: task.depends_on && task.depends_on.length > 0 ? task.depends_on : undefined,
                    modelName: task.model_name || undefined,
                    commentCount: task.comment_count ?? 0,
                    estimatedCostUsd: task.estimated_cost_usd ?? undefined,
                    reflectionCostUsd: task.reflection_cost_usd ?? undefined,
                    usage: task.usage ?? undefined,
                };

                transformedTasks.push(dashTask);
                allTasks.push(dashTask);

                if (task.assignee) {
                    const m = membersMap.get(String(task.assignee.id));
                    if (m) {
                        m.taskCount++;
                        m.totalTimeSpentMs += workTimeMs;
                    }
                }
            }

            const boardMemberIds = (b.member_ids || []).map(String);
            transformedBoards.push({
                id: String(b.id),
                name: b.name,
                isTrial: b.is_trial || false,
                memberIds: boardMemberIds,
                tasks: transformedTasks,
                members: boardMemberIds
                    .map(id => membersMap.get(id))
                    .filter((m): m is Member => !!m),
                lists: ['BACKLOG', 'TODO', 'IN_PROGRESS', 'REVIEW', 'TESTING', 'DONE', 'FAILED'],
                totalActions: allTasks.reduce((sum, t) => sum + t.mutations.length, 0),
                createdAt: b.created_at || new Date().toISOString(),
            });
        }

        const specs: Spec[] = rawSpecs.map(s => this.transformSpec(s));

        const stats: DashboardStats = {
            totalTasks: allTasks.length,
            totalMembers: users.length,
            totalBoards: boards.length,
            completedTasks: allTasks.filter(t => t.currentStatus === 'DONE').length,
            inProgressTasks: allTasks.filter(t => ['IN_PROGRESS', 'REVIEW'].includes(t.currentStatus)).length,
            todoTasks: allTasks.filter(t => ['TODO', 'BACKLOG'].includes(t.currentStatus)).length,
            avgTimeToCompletionMs: 0,
            totalMutations: allTasks.reduce((sum, t) => sum + t.mutations.length, 0),
            mostActiveBoard: transformedBoards[0]?.name || '',
            mostActiveMember: users[0]?.name || '',
        };

        return {
            boards: transformedBoards,
            allTasks,
            allMembers: Array.from(membersMap.values()),
            specs,
            labels,
            generatedAt: new Date().toISOString(),
            stats,
        };
    }

    async fetchTaskDetail(taskId: string): Promise<DashTask> {
        const raw = await this.get<HarnessTaskDetail>(`/api/tasks/${Number(taskId)}/detail/`);
        const history = raw.history || [];
        const mutations = this.transformHistory(history, this.cachedMembers);
        const timeInStatuses = this.computeTimeInStatuses(mutations, raw.created_at);
        const totalLifespanMs = Date.now() - new Date(raw.created_at).getTime();
        const workTimeMs = (timeInStatuses['IN_PROGRESS'] || 0) + (timeInStatuses['REVIEW'] || 0);
        const executingTimeMs = timeInStatuses['EXECUTING'] || 0;

        const assigneeIds = raw.assignee ? [String(raw.assignee.id)] : [];
        const assigneeNames = raw.assignee ? [raw.assignee.name] : [];

        const devEtaHours = raw.dev_eta_seconds ? raw.dev_eta_seconds / 3600 : undefined;
        let remainingTimeMs: number | undefined;
        if (devEtaHours !== undefined) {
            const budgetedMs = devEtaHours * 3600 * 1000;
            const spentMs = timeInStatuses['IN_PROGRESS'] || 0;
            remainingTimeMs = budgetedMs - spentMs;
        }

        const comments: TaskComment[] = (raw.comments || []).map(c => ({
            id: String(c.id),
            taskId: String(c.task_id),
            authorEmail: c.author_email,
            authorLabel: c.author_label,
            content: c.content,
            attachments: c.attachments || [],
            commentType: (c.comment_type as CommentType) || inferTypeFromAttachments(c),
            createdAt: c.created_at,
            fileAttachments: (c.file_attachments || []).map((fa: Record<string, unknown>) => ({
                id: fa.id as number,
                url: fa.url as string,
                originalFilename: fa.original_filename as string,
                contentType: fa.content_type as string,
                fileSize: fa.file_size as number,
                uploadedBy: fa.uploaded_by as string,
                createdAt: fa.created_at as string,
            })),
        }));

        return {
            id: String(raw.id),
            name: raw.title || raw.description.substring(0, 50),
            title: raw.title,
            idShort: raw.id,
            shortLink: String(raw.id),
            boardId: String(raw.board_id),
            boardName: this.cachedBoardNames.get(String(raw.board_id)) || String(raw.board_id),
            currentStatus: raw.status,
            kanbanPosition: raw.kanban_position,
            assignees: assigneeNames,
            assigneeIds: assigneeIds,
            createdAt: raw.created_at,
            createdBy: raw.created_by || 'Unknown',
            mutations,
            comments,
            timeInStatuses,
            totalLifespanMs,
            workTimeMs,
            executingTimeMs,
            description: raw.description,
            devEta: devEtaHours,
            remainingTimeMs,
            isTimerRunning: raw.status === 'IN_PROGRESS',
            priority: raw.priority,
            specId: raw.spec_id ? String(raw.spec_id) : undefined,
            specName: raw.spec_title || undefined,
            labels: raw.labels || [],
            complexity: raw.complexity || undefined,
            metadata: raw.metadata && Object.keys(raw.metadata).length > 0 ? raw.metadata : undefined,
            dependsOn: raw.depends_on && raw.depends_on.length > 0 ? raw.depends_on : undefined,
            modelName: raw.model_name || undefined,
            commentCount: comments.length,
            estimatedCostUsd: raw.estimated_cost_usd ?? undefined,
            reflectionCostUsd: raw.reflection_cost_usd ?? undefined,
            usage: raw.usage ?? undefined,
        };
    }

    async fetchTasksPage(query: TaskListQuery): Promise<PaginatedResponse<DashTask>> {
        await this.ensureBoardAndSpecCaches();
        const qs = this.buildQuery({
            page: query.page,
            page_size: query.page_size,
            search: query.q,
            board_id: query.board,
            status: query.status,
            assignee_id: query.assignee,
            priority: query.priority,
            spec_id: query.spec,
            label_ids: query.labels,
            sort: query.sort,
            created_from: query.created_from,
            created_to: query.created_to,
        });
        const raw = await this.get<PaginatedResponseBody<HarnessTask>>(`/api/tasks/${qs}`);
        return {
            ...raw,
            results: raw.results.map(task => this.transformTask(task, false)),
        };
    }

    async fetchMembersPage(query: MemberListQuery): Promise<PaginatedResponse<Member>> {
        const qs = this.buildQuery({
            page: query.page,
            page_size: query.page_size,
            search: query.q,
            board_id: query.board,
            role: query.role,
            joined_from: query.joined_from,
            joined_to: query.joined_to,
            sort: query.sort,
        });
        const raw = await this.get<PaginatedResponseBody<HarnessUser>>(`/api/members/${qs}`);
        const members = raw.results.map((u, index) => ({
            id: String(u.id),
            fullName: u.name,
            username: u.email.split('@')[0],
            initials: u.name.substring(0, 2).toUpperCase(),
            avatarUrl: null,
            color: u.color || MEMBER_COLORS[index % MEMBER_COLORS.length],
            email: u.email,
            role: u.role || 'HUMAN',
            taskCount: (u as HarnessUser & { task_count?: number }).task_count || 0,
            totalTimeSpentMs: 0,
            availableModels: u.available_models || [],
            cost_tier: u.cost_tier,
            capabilities: u.capabilities,
        }));
        this.cachedMembers = new Map(members.map(m => [m.id, m]));
        return { ...raw, results: members };
    }

    async fetchSpecsPage(query: SpecListQuery): Promise<PaginatedResponse<Spec>> {
        const qs = this.buildQuery({
            page: query.page,
            page_size: query.page_size,
            search: query.q,
            board_id: query.board,
            status: query.status,
            sort: query.sort,
            created_from: query.created_from,
            created_to: query.created_to,
        });
        const raw = await this.get<PaginatedResponseBody<HarnessSpec>>(`/api/specs/${qs}`);
        raw.results.forEach(s => this.cachedSpecTitles.set(String(s.id), s.title));
        return {
            ...raw,
            results: raw.results.map(s => this.transformSpec(s)),
        };
    }

    async fetchBoardsPage(query: { search?: string; sort?: string; page?: number; page_size?: number }): Promise<PaginatedResponse<DashBoard>> {
        const qs = this.buildQuery({
            page: query.page,
            page_size: query.page_size,
            search: query.search,
            sort: query.sort,
        });
        const raw = await this.get<PaginatedResponseBody<HarnessBoard>>(`/api/boards/${qs}`);
        const boards = raw.results.map(b => {
            this.cachedBoardNames.set(String(b.id), b.name);
            const boardMemberIds = (b.member_ids || []).map(String);
            return {
                id: String(b.id),
                name: b.name,
                isTrial: b.is_trial || false,
                workingDir: b.working_dir || null,
                odinInitialized: b.odin_initialized || false,
                memberIds: boardMemberIds,
                tasks: [],
                members: [],
                lists: ['BACKLOG', 'TODO', 'IN_PROGRESS', 'REVIEW', 'TESTING', 'DONE', 'FAILED'],
                totalActions: 0,
                createdAt: b.created_at || new Date().toISOString(),
            };
        });
        return { ...raw, results: boards };
    }

    async fetchTimelinePage(query: TimelineQuery): Promise<PaginatedResponse<DashTask>> {
        await this.ensureBoardAndSpecCaches();
        const qs = this.buildQuery({
            search: query.q,
            board_id: query.board,
            status: query.status,
            assignee_id: query.assignee,
            priority: query.priority,
            date_from: query.date_from,
            date_to: query.date_to,
            sort: query.sort,
        });
        const raw = await this.get<PaginatedResponseBody<HarnessTask> | HarnessTask[]>(`/api/timeline/${qs}`);
        const results = Array.isArray(raw) ? raw : raw.results;
        return {
            count: Array.isArray(raw) ? raw.length : raw.count,
            next: Array.isArray(raw) ? null : raw.next,
            previous: Array.isArray(raw) ? null : raw.previous,
            results: results.map(task => this.transformTask(task, true)),
        };
    }

    async fetchKanban(boardId?: string, query?: { date_from?: string; date_to?: string }): Promise<DashTask[]> {
        await this.ensureBoardAndSpecCaches();
        const qs = this.buildQuery({
            board_id: boardId,
            date_from: query?.date_from,
            date_to: query?.date_to,
        });
        const raw = await this.get<HarnessTask[]>(`/api/kanban/${qs}`);
        return raw.map(task => this.transformTask(task, false));
    }

    async suggestDirectories(query: string, limit: number = 20): Promise<DirectoryEntry[]> {
        const trimmed = query.trim();
        if (!trimmed) return [];
        const qs = this.buildQuery({ q: trimmed, limit });
        const raw = await this.get<DirectoryEntriesResponse>(`/api/runtime/directories/suggest/${qs}`);
        return raw.entries || [];
    }

    async checkDirectory(path: string): Promise<import('../integration/IntegrationService').DirectoryCheckResult> {
        const trimmed = path.trim();
        if (!trimmed) return { odin_exists: false, linked_board: null, can_init: false, message: '' };
        const qs = this.buildQuery({ path: trimmed });
        return this.get(`/api/boards/check-dir/${qs}`);
    }

    async listDirectoryChildren(path: string, limit: number = 100): Promise<DirectoryEntry[]> {
        const trimmed = path.trim();
        if (!trimmed) return [];
        const qs = this.buildQuery({ path: trimmed, limit });
        const raw = await this.get<DirectoryEntriesResponse>(`/api/runtime/directories/children/${qs}`);
        return raw.entries || [];
    }

    async updateTaskAssignees(taskId: string, memberIds: string[]): Promise<void> {
        const email = this.baseUrl.includes('localhost') ? 'admin@example.com' : 'unknown@example.com';
        const id = Number(taskId);
        if (memberIds.length === 0) {
            await this.post(`/api/tasks/${id}/unassign/`, { updated_by: email });
        } else {
            const assignee_id = Number(memberIds[0]);
            await this.post(`/api/tasks/${id}/assign/`, { assignee_id, updated_by: email });
        }
    }

    async createBoard(name: string, description?: string, workingDir?: string, disabledAgents?: string[]): Promise<unknown> {
        const body: Record<string, unknown> = { name, description };
        if (workingDir) body.working_dir = workingDir;
        if (disabledAgents && disabledAgents.length > 0) body.disabled_agents = disabledAgents;
        return this.post('/api/boards/', body);
    }

    async deleteBoard(boardId: string): Promise<void> {
        await this.del(`/api/boards/${Number(boardId)}/`);
    }

    async initOdin(boardId: string): Promise<unknown> {
        return this.post(`/api/boards/${boardId}/init-odin/`, {});
    }

    async updateBoard(boardId: string, updates: Record<string, unknown>): Promise<unknown> {
        return this.post(`/api/boards/${boardId}/`, updates, 'PATCH');
    }

    async createTask(
        boardId: string, title: string, description: string,
        priority: string = 'MEDIUM', createdBy?: string, devEta?: number,
        options?: {
            createdByUserId?: number;
            assigneeId?: number;
            modelName?: string;
            labelIds?: number[];
            workingDir?: string;
        }
    ): Promise<unknown> {
        const body: Record<string, unknown> = {
            board_id: Number(boardId), title, description, priority,
        };
        if (options?.createdByUserId) body.created_by_user_id = options.createdByUserId;
        else body.created_by = createdBy || 'admin@example.com';
        if (devEta !== undefined) body.dev_eta_seconds = Math.round(devEta * 3600);
        if (options?.assigneeId) body.assignee_id = options.assigneeId;
        if (options?.modelName) body.model_name = options.modelName;
        if (options?.labelIds && options.labelIds.length > 0) body.label_ids = options.labelIds;
        if (options?.workingDir) body.metadata = { working_dir: options.workingDir };

        return this.post<HarnessTask>('/api/tasks/', body);
    }

    async updateTask(taskId: string, updates: {
        title?: string; description?: string; priority?: string; devEta?: number; status?: string;
        labelIds?: number[]; modelName?: string; kanbanTargetIndex?: number; kanbanTargetStatus?: string;
    }): Promise<void> {
        const email = this.baseUrl.includes('localhost') ? 'admin@example.com' : 'unknown@example.com';
        const body: Record<string, unknown> = { ...updates, updated_by: email };
        if (updates.devEta !== undefined) {
            body.dev_eta_seconds = Math.round(updates.devEta * 3600);
            delete body.devEta;
        }
        if (updates.labelIds !== undefined) {
            body.label_ids = updates.labelIds;
            delete body.labelIds;
        }
        if (updates.modelName !== undefined) {
            body.model_name = updates.modelName;
            delete body.modelName;
        }
        if (updates.kanbanTargetIndex !== undefined) {
            body.kanban_target_index = updates.kanbanTargetIndex;
            delete body.kanbanTargetIndex;
        }
        if (updates.kanbanTargetStatus !== undefined) {
            body.kanban_target_status = updates.kanbanTargetStatus;
            delete body.kanbanTargetStatus;
        }
        await this.post(`/api/tasks/${Number(taskId)}/`, body, 'PUT');
    }

    async stopExecution(taskId: string, targetStatus: string): Promise<void> {
        const email = this.baseUrl.includes('localhost') ? 'admin@example.com' : 'unknown@example.com';
        await this.post(`/api/tasks/${Number(taskId)}/stop_execution/`, {
            updated_by: email,
            target_status: targetStatus,
            reason: 'user_drag_stop_confirm',
        });
    }

    async stopRuntimeTask(taskId: string, targetStatus = 'TODO'): Promise<void> {
        const email = this.baseUrl.includes('localhost') ? 'admin@example.com' : 'unknown@example.com';
        await this.post('/api/runtime/stop/', {
            task_id: Number(taskId),
            updated_by: email,
            target_status: targetStatus,
            reason: 'runtime_monitor_stop',
        });
    }

    async fetchOdinStatus(params?: { spec?: string; agent?: string; status?: string }): Promise<OdinStatusResponse> {
        const qs = new URLSearchParams();
        if (params?.spec) qs.set('spec', params.spec);
        if (params?.agent) qs.set('agent', params.agent);
        if (params?.status) qs.set('status', params.status);
        return this.get<OdinStatusResponse>(`/api/runtime/odin-status/${qs.toString() ? `?${qs}` : ''}`);
    }

    async fetchProcessMonitor(params?: { boardId?: string; specId?: string; runningOnly?: boolean }): Promise<ProcessMonitorResponse> {
        const qs = new URLSearchParams();
        if (params?.boardId) qs.set('board_id', params.boardId);
        if (params?.specId) qs.set('spec_id', params.specId);
        if (params?.runningOnly !== undefined) qs.set('running_only', String(params.runningOnly));
        return this.get<ProcessMonitorResponse>(`/api/runtime/process-monitor/${qs.toString() ? `?${qs}` : ''}`);
    }

    async getLabels(): Promise<Label[]> {
        const payload = await this.get<PaginatedResponseBody<Label> | Label[]>('/api/labels/?page_size=500');
        return this.extractList(payload);
    }

    async createLabel(name: string, color: string): Promise<Label> {
        return this.post<Label>('/api/labels/', { name, color });
    }

    async addTaskLabel(taskId: string, labelId: number): Promise<void> {
        const email = this.baseUrl.includes('localhost') ? 'admin@example.com' : 'unknown@example.com';
        await this.post(`/api/tasks/${Number(taskId)}/labels/`, { label_ids: [labelId], updated_by: email });
    }

    async removeTaskLabel(taskId: string, labelId: number): Promise<void> {
        const email = this.baseUrl.includes('localhost') ? 'admin@example.com' : 'unknown@example.com';
        await this.delWithBody(`/api/tasks/${Number(taskId)}/labels/`, { label_ids: [labelId], updated_by: email });
    }

    async addComment(taskId: string, content: string, authorEmail?: string, commentType?: CommentType): Promise<void> {
        const email = authorEmail || (this.baseUrl.includes('localhost') ? 'admin@example.com' : 'unknown@example.com');
        const body: Record<string, unknown> = { author_email: email, content };
        if (commentType) body.comment_type = commentType;
        await this.post(`/api/tasks/${Number(taskId)}/comments/`, body);
    }

    async replyToQuestion(taskId: string, questionCommentId: string, content: string, authorEmail?: string): Promise<void> {
        const email = authorEmail || (this.baseUrl.includes('localhost') ? 'admin@example.com' : 'unknown@example.com');
        await this.post(`/api/tasks/${Number(taskId)}/comments/${questionCommentId}/reply/`, {
            author_email: email,
            content,
        });
    }

    async triggerReflection(taskId: string, params: ReflectionRequest): Promise<ReflectionReport> {
        return this.post<ReflectionReport>(`/tasks/${Number(taskId)}/reflect/`, params);
    }

    async fetchReflections(taskId: string): Promise<ReflectionReport[]> {
        return this.get<ReflectionReport[]>(`/tasks/${Number(taskId)}/reflections/`);
    }

    async fetchAllReflections(params?: { status?: string; verdict?: string; board?: string }): Promise<ReflectionReport[]> {
        const searchParams = new URLSearchParams();
        if (params?.status) searchParams.set('status', params.status);
        if (params?.verdict) searchParams.set('verdict', params.verdict);
        if (params?.board) searchParams.set('board', params.board);
        const qs = searchParams.toString();
        return this.get<ReflectionReport[]>(`/reflections/${qs ? `?${qs}` : ''}`);
    }

    async fetchReflectionById(reportId: number): Promise<ReflectionReport> {
        return this.get<ReflectionReport>(`/reflections/${reportId}/`);
    }

    async cancelReflection(reportId: number): Promise<ReflectionReport> {
        return this.post<ReflectionReport>(`/reflections/${reportId}/cancel/`, {});
    }

    async deleteReflection(reportId: number): Promise<void> {
        await this.del(`/reflections/${reportId}/`);
    }

    async fetchBoardAgents(boardId: string): Promise<AgentConfig[]> {
        const resp = await this.get<{ agents: AgentConfig[] }>(`/api/boards/${Number(boardId)}/agents/`);
        return resp.agents;
    }

    async toggleBoardAgent(boardId: string, agentName: string, enabled: boolean): Promise<{ name: string; enabled: boolean; board?: Record<string, unknown> }> {
        return this.post<{ name: string; enabled: boolean; board?: Record<string, unknown> }>(
            `/api/boards/${Number(boardId)}/agents/${encodeURIComponent(agentName)}/`,
            { enabled },
            'PATCH',
        );
    }

    async toggleBoardModel(boardId: string, agentName: string, modelName: string, enabled: boolean): Promise<void> {
        await this.post(
            `/api/boards/${Number(boardId)}/agents/${encodeURIComponent(agentName)}/models/${encodeURIComponent(modelName)}/`,
            { enabled },
            'PATCH',
        );
    }

    async fetchPresets(): Promise<PresetsResponse> {
        return this.get<PresetsResponse>('/api/presets/');
    }

    getAvailableStatuses(): string[] {
        return ['BACKLOG', 'TODO', 'IN_PROGRESS', 'REVIEW', 'TESTING', 'DONE', 'FAILED'];
    }

    async createUser(name: string, email: string, color?: string): Promise<HarnessUser> {
        return this.post<HarnessUser>('/api/users/', { name, email, color });
    }

    async updateUser(id: string, name?: string, email?: string, color?: string, availableModels?: Array<{name: string; description: string; is_default: boolean}>): Promise<HarnessUser> {
        const body: Record<string, unknown> = { name, email, color };
        if (availableModels !== undefined) {
            body.available_models = availableModels;
        }
        return this.post<HarnessUser>(`/api/users/${id}/`, body, 'PUT');
    }

    async deleteUser(id: string): Promise<void> {
        await this.del(`/api/users/${id}/`);
    }

    async fetchUsers(): Promise<HarnessUser[]> {
        const resp = await this.get<PaginatedResponseBody<HarnessUser>>('/api/users/?page_size=200');
        return resp.results;
    }

    async fetchSpecs(): Promise<Spec[]> {
        const raw = await this.get<PaginatedResponseBody<HarnessSpec>>('/api/specs/?page_size=500');
        raw.results.forEach(s => this.cachedSpecTitles.set(String(s.id), s.title));
        return raw.results.map(s => this.transformSpec(s));
    }

    async fetchSpecDetail(id: string): Promise<Spec> {
        const raw = await this.get<HarnessSpec>(`/api/specs/${id}/`);
        return this.transformSpec(raw);
    }

    private buildQuery(params: Record<string, unknown>): string {
        const searchParams = new URLSearchParams();
        Object.entries(params).forEach(([key, value]) => {
            if (value === undefined || value === null || value === '') return;
            if (Array.isArray(value)) {
                if (value.length === 0) return;
                searchParams.set(key, value.join(','));
                return;
            }
            searchParams.set(key, String(value));
        });
        const query = searchParams.toString();
        return query ? `?${query}` : '';
    }

    private async ensureBoardAndSpecCaches(): Promise<void> {
        const calls: Array<Promise<unknown>> = [];
        if (this.cachedBoardNames.size === 0) {
            calls.push(
                this.get<PaginatedResponseBody<HarnessBoard>>('/api/boards/?page_size=200').then(resp => {
                    resp.results.forEach(b => this.cachedBoardNames.set(String(b.id), b.name));
                }),
            );
        }
        if (this.cachedSpecTitles.size === 0) {
            calls.push(
                this.get<PaginatedResponseBody<HarnessSpec>>('/api/specs/?page_size=500').then(resp => {
                    resp.results.forEach(s => this.cachedSpecTitles.set(String(s.id), s.title));
                }),
            );
        }
        if (calls.length > 0) {
            await Promise.all(calls);
        }
    }

    private transformTask(task: HarnessTask, includeHistory: boolean): DashTask {
        if (task.assignee) {
            const memberId = String(task.assignee.id);
            if (!this.cachedMembers.has(memberId)) {
                this.cachedMembers.set(memberId, {
                    id: memberId,
                    fullName: task.assignee.name,
                    username: task.assignee.email.split('@')[0],
                    initials: task.assignee.name.substring(0, 2).toUpperCase(),
                    avatarUrl: null,
                    color: task.assignee.color || MEMBER_COLORS[0],
                    email: task.assignee.email,
                    role: task.assignee.role || 'HUMAN',
                    taskCount: 0,
                    totalTimeSpentMs: 0,
                    availableModels: task.assignee.available_models || [],
                    cost_tier: task.assignee.cost_tier,
                    capabilities: task.assignee.capabilities,
                });
            }
        }

        const history = includeHistory ? (task.history || []) : (task.history || []);
        const mutations = this.transformHistory(history, this.cachedMembers);
        const timeInStatuses = this.computeTimeInStatuses(mutations, task.created_at);
        const totalLifespanMs = Date.now() - new Date(task.created_at).getTime();
        const workTimeMs = (timeInStatuses['IN_PROGRESS'] || 0) + (timeInStatuses['REVIEW'] || 0);
        const executingTimeMs = timeInStatuses['EXECUTING'] || 0;
        const assigneeIds = task.assignee ? [String(task.assignee.id)] : [];
        const assigneeNames = task.assignee ? [task.assignee.name] : [];
        const devEtaHours = task.dev_eta_seconds ? task.dev_eta_seconds / 3600 : undefined;

        let remainingTimeMs: number | undefined;
        if (devEtaHours !== undefined) {
            const budgetedMs = devEtaHours * 3600 * 1000;
            const spentMs = timeInStatuses['IN_PROGRESS'] || 0;
            remainingTimeMs = budgetedMs - spentMs;
        }

        const boardId = String(task.board_id);
        const specId = task.spec_id ? String(task.spec_id) : undefined;
        return {
            id: String(task.id),
            name: task.title || task.description.substring(0, 50),
            title: task.title,
            idShort: task.id,
            shortLink: String(task.id),
            boardId,
            boardName: this.cachedBoardNames.get(boardId) || boardId,
            currentStatus: task.status,
            kanbanPosition: task.kanban_position,
            assignees: assigneeNames,
            assigneeIds,
            createdAt: task.created_at,
            createdBy: task.created_by || 'Unknown',
            mutations,
            comments: [],
            timeInStatuses,
            totalLifespanMs,
            workTimeMs,
            executingTimeMs,
            description: task.description,
            devEta: devEtaHours,
            remainingTimeMs,
            isTimerRunning: task.status === 'IN_PROGRESS',
            priority: task.priority,
            specId,
            specName: specId ? this.cachedSpecTitles.get(specId) : undefined,
            labels: task.labels || [],
            complexity: task.complexity || undefined,
            metadata: task.metadata && Object.keys(task.metadata).length > 0 ? task.metadata : undefined,
            dependsOn: task.depends_on && task.depends_on.length > 0 ? task.depends_on : undefined,
            modelName: task.model_name || undefined,
            commentCount: task.comment_count ?? 0,
            estimatedCostUsd: task.estimated_cost_usd ?? undefined,
            reflectionCostUsd: task.reflection_cost_usd ?? undefined,
            usage: task.usage ?? undefined,
        };
    }

    private transformSpec(s: HarnessSpec): Spec {
        const tasks = s.tasks || [];
        const comments: SpecComment[] = (s.comments || []).map(c => ({
            id: String(c.id),
            specId: String(c.spec_id),
            authorEmail: c.author_email,
            authorLabel: c.author_label,
            content: c.content,
            attachments: c.attachments || [],
            commentType: (c.comment_type as CommentType) || 'status_update',
            createdAt: c.created_at,
        }));
        return {
            id: String(s.id),

            title: s.title,
            source: s.source,
            content: s.content,
            abandoned: s.abandoned,
            boardId: String(s.board_id),
            metadata: s.metadata,
            createdAt: s.created_at,
            cwd: typeof s.metadata?.working_dir === 'string' ? s.metadata.working_dir : undefined,
            costSummary: s.cost_summary || undefined,
            taskCount: s.task_count ?? tasks.length,
            comments,
            tasks: tasks.map(t => ({
                id: String(t.id),
                name: t.title || t.description.substring(0, 50),
                title: t.title,
                idShort: t.id,
                shortLink: String(t.id),
                boardId: String(t.board_id),
                boardName: '',
                currentStatus: t.status,
                kanbanPosition: t.kanban_position,
                assignees: t.assignee ? [t.assignee.name] : [],
                assigneeIds: t.assignee ? [String(t.assignee.id)] : [],
                createdAt: t.created_at,
                createdBy: t.created_by || 'Unknown',
                mutations: [],
                comments: [],
                timeInStatuses: t.time_in_statuses || {},
                totalLifespanMs: Date.now() - new Date(t.created_at).getTime(),
                workTimeMs: (t.time_in_statuses?.['IN_PROGRESS'] || 0) + (t.time_in_statuses?.['REVIEW'] || 0),
                executingTimeMs: t.time_in_statuses?.['EXECUTING'] || 0,
                description: t.description,
                priority: t.priority,
                specId: String(s.id),
                labels: t.labels || [],
                complexity: t.complexity || undefined,
                metadata: t.metadata && Object.keys(t.metadata).length > 0 ? t.metadata : undefined,
                dependsOn: t.depends_on && t.depends_on.length > 0 ? t.depends_on : undefined,
                modelName: t.model_name || undefined,
                estimatedCostUsd: t.estimated_cost_usd ?? undefined,
                reflectionCostUsd: t.reflection_cost_usd ?? undefined,
                usage: t.usage ?? undefined,
            })),
        };
    }

    private transformHistory(history: HarnessTaskHistory[], members: Map<string, Member>): TaskMutation[] {
        const emailToMember = new Map<string, Member>();
        members.forEach(m => {
            if (m.username.includes('@')) emailToMember.set(m.username, m);
        });

        return history.map(h => {
            const member = h.changed_by ? (members.get(h.changed_by) || emailToMember.get(h.changed_by)) : undefined;
            const actor = member ? member.fullName : (h.changed_by || 'Unknown');
            let type: TaskMutation['type'] = 'other';
            if (h.field_name === 'status') type = 'status_change';
            if (h.field_name === 'assignee_id') type = 'assigned';
            if (h.field_name === 'description') type = 'description_update';

            let oldValue = h.old_value;
            let newValue = h.new_value;
            if (h.field_name === 'assignee_id') {
                oldValue = h.old_value && members.has(String(h.old_value)) ? members.get(String(h.old_value))!.fullName : (h.old_value || 'None');
                newValue = h.new_value && members.has(String(h.new_value)) ? members.get(String(h.new_value))!.fullName : (h.new_value || 'None');
            }

            return {
                id: String(h.id),
                type,
                date: h.changed_at,
                timestamp: new Date(h.changed_at).getTime(),
                actor,
                actorId: h.changed_by || '',
                description: `${h.field_name} changed from ${oldValue} to ${newValue}`,
                fromStatus: h.field_name === 'status' ? h.old_value : undefined,
                toStatus: h.field_name === 'status' ? h.new_value : undefined,
                assignedMember: h.field_name === 'assignee_id' ? (members.get(String(h.new_value))?.fullName) : undefined,
                fieldName: h.field_name,
                oldValue: h.old_value,
                newValue: h.new_value,
            };
        });
    }

    private computeTimeInStatuses(mutations: TaskMutation[], createdAt: string): Record<string, number> {
        const sorted = [...mutations]
            .filter(m => m.type === 'status_change')
            .sort((a, b) => a.timestamp - b.timestamp);

        const times: Record<string, number> = {};
        let lastTime = new Date(createdAt).getTime();
        let currentStatus = 'TODO';

        for (const m of sorted) {
            const duration = m.timestamp - lastTime;
            times[currentStatus] = (times[currentStatus] || 0) + duration;
            currentStatus = m.toStatus || currentStatus;
            lastTime = m.timestamp;
        }

        times[currentStatus] = (times[currentStatus] || 0) + (Date.now() - lastTime);
        return times;
    }

    private async authHeaders(): Promise<Record<string, string>> {
        if (!this.tokenProvider) return {};
        const token = await this.tokenProvider();
        if (!token) return {};
        return { Authorization: `Bearer ${token}` };
    }

    private async get<T>(path: string): Promise<T> {
        const headers = await this.authHeaders();
        const res = await fetch(`${this.baseUrl}${path}`, { headers });
        if (!res.ok) {
            let body: unknown;
            try { body = await res.json(); } catch { /* ignore */ }
            throw new ApiError(res.status, res.statusText, body);
        }
        return res.json();
    }

    async deleteTask(taskId: string): Promise<void> {
        await this.del(`/api/tasks/${Number(taskId)}/`);
    }

    async deleteSpec(specId: string): Promise<void> {
        await this.del(`/api/specs/${Number(specId)}/`);
    }

    async clearBoard(boardId: string): Promise<{ tasks_deleted: number; specs_deleted: number }> {
        return this.post(`/api/boards/${Number(boardId)}/clear/`, {});
    }

    async addBoardMembers(boardId: string, userIds: string[]): Promise<void> {
        await this.post(`/api/boards/${Number(boardId)}/members/add/`, {
            user_ids: userIds.map(Number),
        });
    }

    async removeBoardMembers(boardId: string, userIds: string[]): Promise<void> {
        await this.post(`/api/boards/${Number(boardId)}/members/remove/`, {
            user_ids: userIds.map(Number),
        });
    }

    async fetchSpecDiagnostic(specId: string): Promise<unknown> {
        return this.get(`/api/specs/${Number(specId)}/diagnostic/`);
    }

    async cloneSpec(specId: string): Promise<Spec> {
        const raw = await this.post<HarnessSpec>(`/api/specs/${Number(specId)}/clone/`, {});
        return this.transformSpec(raw);
    }

    async summarizeTask(taskId: string): Promise<void> {
        await this.post<{ status: string }>(`/tasks/${Number(taskId)}/summarize/`, {});
    }

    private async post<T>(path: string, body: unknown, method: 'POST' | 'PUT' | 'PATCH' = 'POST'): Promise<T> {
        const auth = await this.authHeaders();
        const res = await fetch(`${this.baseUrl}${path}`, {
            method,
            headers: { 'Content-Type': 'application/json', ...auth },
            body: JSON.stringify(body)
        });
        if (!res.ok) {
            let responseBody: unknown;
            try { responseBody = await res.json(); } catch { /* ignore */ }
            throw new ApiError(res.status, res.statusText, responseBody);
        }
        return res.json();
    }

    private async del(path: string): Promise<void> {
        const auth = await this.authHeaders();
        const res = await fetch(`${this.baseUrl}${path}`, { method: 'DELETE', headers: auth });
        if (!res.ok) {
            let body: unknown;
            try { body = await res.json(); } catch { /* ignore */ }
            throw new ApiError(res.status, res.statusText, body);
        }
    }

    private async delWithBody(path: string, body: unknown): Promise<void> {
        const auth = await this.authHeaders();
        const res = await fetch(`${this.baseUrl}${path}`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json', ...auth },
            body: JSON.stringify(body)
        });
        if (!res.ok) {
            let responseBody: unknown;
            try { responseBody = await res.json(); } catch { /* ignore */ }
            throw new ApiError(res.status, res.statusText, responseBody);
        }
    }
}
