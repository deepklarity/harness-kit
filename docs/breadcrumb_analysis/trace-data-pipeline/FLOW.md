# Trace Data Pipeline

Trigger: Harness subprocess writes stream-json to stdout during task execution
End state: TraceViewer in frontend renders timeline + token summary; cost displayed on task cards

## Flow

### Phase 1: Trace Capture (Odin)

```
orchestrator.py :: _execute_task()
  -> creates paths:
       .odin/logs/task_{task_id}.out        (plain text)
       .odin/logs/task_{task_id}.trace.jsonl (raw JSONL)
  -> passes both in context dict to harness

harness.execute(prompt, context)
  -> spawns subprocess with stream-json output flag
  -> calls base.read_with_trace(proc, output_file, trace_file)

base.py :: read_with_trace()
  -> reads subprocess stdout line by line
  -> for each line:
       writes raw JSON to trace_file (flush)
       extracts text via extract_text_from_line()
       writes extracted text to output_file (flush)
  -> returns accumulated plain text

[Claude only]
claude.py :: _extract_token_usage(raw_output)
  -> parses modelUsage aggregate (preferred) or sums step_finish events
  -> returns {input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, total_tokens}
  -> stored in TaskResult.metadata["usage"]
```

### Phase 2: Trace Ingestion (Orchestrator -> Backend)

```
orchestrator.py :: _execute_task() (post-execution)
  -> reads trace_file (falls back to output_file, then result.output)
  -> stores trace_file path in task.metadata["trace_file"]

  -> task_mgr.record_execution_result(
       raw_output=raw_jsonl,          # full trace
       effective_input=prompt[:5000],  # truncated prompt
       success=bool,
       duration_ms=int,
       metadata={selected_model, estimated_cost_usd, ...}
     )

  -> task_mgr.add_comment(
       content=raw_jsonl,
       attachments=["trace:execution_jsonl"]
     )
```

### Phase 3: Backend Processing

```
views.py :: execution_result() endpoint
  -> extract_agent_text(raw_output) -> (clean_text, extracted_usage)
  -> parse_envelope(clean_text) -> (output, success, summary)
  -> compose_comment(duration, metadata, summary) -> formatted comment

  -> stores in task.metadata:
       last_duration_ms, selected_model, full_output, effective_input
       total_estimated_cost_usd (accumulated across retries)
       failure_type/reason/origin (if failed)

  -> creates TaskComment with metrics inline (source of truth for usage)

serializers.py :: TaskSerializer
  -> usage: computed on-the-fly from trace comments via compute_usage_from_trace()
  -> estimated_cost_usd: computed from model pricing + usage via compute_task_estimated_cost()
```

### Phase 4: Frontend Display

```
TraceViewer.tsx :: parseTrace(raw)
  -> splits raw JSONL by newlines, parses each as JSON
  -> returns TraceEvent[]

TraceViewer.tsx :: detectTraceFormat(events)
  -> 'claude_code': has content arrays, step_finish, modelUsage
  -> 'odin': has action + run_id fields
  -> 'unknown': fallback

TraceViewer.tsx :: extractTokenSummary(events)
  -> [claude_code]: reads modelUsage event (aggregate per model)
  -> [fallback]: sums step_finish token events

  [claude_code format]
  buildClaudeCodeTimeline(events) -> timeline items (tool_use, tool_result, text)

  [odin format]
  buildOdinTimeline(events) -> timeline items (phases, task lifecycle, durations)

costEstimation.ts :: formatCost()
  -> "$X.XX" | "< $0.01" | "---" (display only, computation is backend-side)

diagnostics.ts :: parseMetricsFromComment()
  -> regex extracts duration + tokens from comment text
  -> used for attempt-level metrics when structured data unavailable
```

## Trace Formats by Harness

| Harness | CLI Flag | JSON Structure | Token Extraction |
|---------|----------|---------------|-----------------|
| Claude | `--output-format stream-json --verbose` | `content_block_delta`, `step_finish`, `modelUsage` | Yes (harness-side) |
| Gemini | `--output-format stream-json` | `{"type": "text", "text": "..."}` | No |
| Qwen | `--output-format stream-json` | `{"type": "assistant", "message": {"content": [...]}}` | No |
| MiniMax | `--format json` | `{"type": "step_finish", "content": "..."}` | No |
| GLM | `--format json` | `{"type": "step_finish", "content": "..."}` | No |
| Codex | (none) | Plain text, no JSON | No |

## E2E Snapshot Data

```
tests/e2e_snapshots/full_harness_smoke/
  -> snapshot.json   (239 KB, unified)
  -> spec.json       (spec metadata)
  -> tasks.json      (7 tasks, all fields including metadata with usage/duration/cost)
  -> comments.json   (42 comments, includes trace comments)
  -> history.json    (64 status mutations)
  -> summary.json    (aggregates: 547K tokens, 297s, 6 harnesses)

Captured from: odin/sample_specs/full_harness_smoke_spec.md (spec #38)
Exercises: qwen, gemini, claude, minimax, codex, glm + assembly task with dependencies
```

## See also

- `harness-isolation-testing/` -- harness CLI construction and MCP config
- `spec-task-lifecycle/02-execute-and-dispatch/` -- task dispatch and status transitions
