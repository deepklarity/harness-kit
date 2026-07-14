import { ReplyBox } from './factory/ReplyBox';

interface TaskReworkBoxProps {
    status: string;
    onRework: (instruction: string) => Promise<void>;
}

/**
 * Point at any shelved (TESTING) task, type one sentence of intent, and the
 * kit composes + dispatches a follow-up task (task #259) — no model call,
 * pure assembly from the parent's context + the instruction. Reuses the
 * same ReplyBox building block as the Inbox shelf's Rework box so both
 * surfaces stay in lockstep.
 */
export function TaskReworkBox({ status, onRework }: TaskReworkBoxProps) {
    if (status !== 'TESTING') return null;

    return (
        <div className="mb-8">
            <h3 className="text-sm font-bold text-muted-foreground uppercase tracking-wider mb-3">
                Rework
            </h3>
            <p className="text-xs text-muted-foreground mb-2">
                One sentence of intent — the kit composes and dispatches a follow-up task from this one.
            </p>
            <ReplyBox
                onSubmit={onRework}
                placeholder="e.g. Add a dark-mode toggle to the settings page…"
                buttonLabel="Rework"
            />
        </div>
    );
}
