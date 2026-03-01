/**
 * Tests for parseCommentBody — verifies clean separation of human summary
 * from machine trace data across different harness output formats.
 *
 * Sample data from odin/tests/sample_data/comments/
 */
import { describe, it, expect } from 'vitest';
import { parseCommentBody } from './commentParser';

describe('parseCommentBody', () => {
    it('passes through clean mock output unchanged', () => {
        const raw = 'Mock task completed successfully.';
        const { summary, traceData } = parseCommentBody(raw);
        expect(summary).toBe('Mock task completed successfully.');
        expect(traceData).toBeNull();
    });

    it('passes through clean minimax output unchanged', () => {
        const raw = 'Completed successfully';
        const { summary, traceData } = parseCommentBody(raw);
        expect(summary).toBe('Completed successfully');
        expect(traceData).toBeNull();
    });

    it('strips inline ","time":{...} from minimax/claude output', () => {
        const raw = String.raw`\nWrote a 2-line reflective paragraph about technology for a collaborative poem, including the HTML <mark>minimax</mark> tag to highlight the agent's name, saved to minimax.html in the current working directory.","time":{"start":1771527165675,"end":1771527165675}}}
{"type":"step_finish","timestamp":1771527165719,"sessionID":"ses_388c15eb5ffelmw5Xwh0NBzU1U","part":{"id":"prt_c773f56ec001hCUaGajyXtyF6b","sessionID":"ses_388c15eb5ffelmw5Xwh0NBzU1U","messageID":"msg_c773f4682001vhrAl29cGg1mRB","type":"step-finish","reason":"stop","snapshot":"4b825dc642cb6eb9a060e54bf8d69288fbee4904","cost":0,"tokens":{"total":14024,"input":81,"output":150,"reasoning":0,"cache":{"read":13296,"write":497}}}}`;

        const { summary, traceData } = parseCommentBody(raw);
        expect(summary).toBe(
            "Wrote a 2-line reflective paragraph about technology for a collaborative poem, including the HTML <mark>minimax</mark> tag to highlight the agent's name, saved to minimax.html in the current working directory."
        );
        expect(traceData).not.toBeNull();
        expect(traceData).toContain('step_finish');
        expect(traceData).toContain('"time":');
    });

    it('strips inline ","usage":{...} from qwen output', () => {
        const raw = String.raw`\nCreated a 2-line technology paragraph with ` + '`<mark>qwen</mark>`' + String.raw` highlighting and saved it to qwen.html as a continuation stanza for the collaborative poem.","usage":{"input_tokens":25540,"output_tokens":276,"cache_read_input_tokens":24771,"total_tokens":25816},"permission_denials":[]}`;

        const { summary, traceData } = parseCommentBody(raw);
        expect(summary).toBe(
            'Created a 2-line technology paragraph with `<mark>qwen</mark>` highlighting and saved it to qwen.html as a continuation stanza for the collaborative poem.'
        );
        expect(traceData).not.toBeNull();
        expect(traceData).toContain('usage');
        expect(traceData).toContain('permission_denials');
    });

    it('strips leading literal backslash-n', () => {
        const raw = String.raw`\nSome summary text.`;
        const { summary } = parseCommentBody(raw);
        expect(summary).toBe('Some summary text.');
    });

    it('handles empty string', () => {
        const { summary, traceData } = parseCommentBody('');
        expect(summary).toBe('');
        expect(traceData).toBeNull();
    });

    it('handles multi-line human text without trace', () => {
        const raw = 'First line of summary.\nSecond line with details.\nThird line.';
        const { summary, traceData } = parseCommentBody(raw);
        expect(summary).toBe('First line of summary.\nSecond line with details.\nThird line.');
        expect(traceData).toBeNull();
    });

    it('does not strip regular JSON that is not trace data', () => {
        const raw = 'Created config:\n{"name": "test", "value": 42}';
        const { summary, traceData } = parseCommentBody(raw);
        // Regular JSON without trace fields should be kept in summary
        expect(summary).toBe('Created config:\n{"name": "test", "value": 42}');
        expect(traceData).toBeNull();
    });
});
