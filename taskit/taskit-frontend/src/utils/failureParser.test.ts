import { describe, expect, it } from 'vitest';

import { parseFailureDetails } from './failureParser';

describe('parseFailureDetails', () => {
    it('extracts structured failure fields', () => {
        const parsed = parseFailureDetails(
            [
                'Failed: something broke',
                'Failure type: agent_execution_failure',
                'Reason: odin exec exited with code 1',
                'Origin: taskit_dag_executor',
            ].join('\n')
        );

        expect(parsed.failureType).toBe('agent_execution_failure');
        expect(parsed.failureReason).toBe('odin exec exited with code 1');
        expect(parsed.failureOrigin).toBe('taskit_dag_executor');
        expect(parsed.failureDebug).toBeNull();
        expect(parsed.displaySummary).toBe('Failed: something broke');
    });

    it('captures multiline debug blocks without leaking into summary', () => {
        const parsed = parseFailureDetails(
            [
                'Failed: auth issue',
                'Failure type: backend_auth_failure',
                'Reason: TaskIt returned 401 Unauthorized',
                'Origin: taskit_dag_executor',
                'Debug: Authentication error: TaskIt returned 401 Unauthorized',
                'The TaskIt backend requires authentication.',
                'Set ODIN_ADMIN_USER and ODIN_ADMIN_PASSWORD.',
            ].join('\n')
        );

        expect(parsed.failureType).toBe('backend_auth_failure');
        expect(parsed.failureReason).toBe('TaskIt returned 401 Unauthorized');
        expect(parsed.failureOrigin).toBe('taskit_dag_executor');
        expect(parsed.failureDebug).toContain('Authentication error: TaskIt returned 401 Unauthorized');
        expect(parsed.failureDebug).toContain('Set ODIN_ADMIN_USER and ODIN_ADMIN_PASSWORD.');
        expect(parsed.displaySummary).toBe('Failed: auth issue');
    });
});
