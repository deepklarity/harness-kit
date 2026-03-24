import type { AnalyticsCostSummary } from '../../types';

export function exportAnalyticsCsv(data: AnalyticsCostSummary): void {
    const lines: string[] = [];

    // Summary KPIs
    lines.push('Section,Metric,Value');
    lines.push(`KPI,Total Spend,$${data.summary_kpis.total_spend.toFixed(4)}`);
    lines.push(`KPI,Total Tokens,${data.summary_kpis.total_tokens}`);
    lines.push(`KPI,Task Count,${data.summary_kpis.task_count}`);
    lines.push(`KPI,Avg Cost/Task,$${data.summary_kpis.avg_cost_per_task.toFixed(4)}`);
    lines.push(`KPI,Reflection Cost,$${data.summary_kpis.reflection_cost.toFixed(4)}`);
    lines.push('');

    // Cost by model
    lines.push('Model,Cost,Tokens,Tasks');
    for (const m of data.cost_by_model) {
        lines.push(`${m.model},$${m.cost.toFixed(4)},${m.tokens},${m.task_count}`);
    }
    lines.push('');

    // Cost by board
    lines.push('Board,Cost,Tasks');
    for (const b of data.cost_by_board) {
        lines.push(`"${b.board_name}",$${b.cost.toFixed(4)},${b.task_count}`);
    }
    lines.push('');

    // Time series
    lines.push('Date,Total Cost');
    for (const ts of data.time_series) {
        lines.push(`${ts.date},$${ts.total.toFixed(4)}`);
    }
    lines.push('');

    // Top expensive tasks
    lines.push('Task ID,Title,Model,Status,Cost,Tokens');
    for (const t of data.top_expensive_tasks) {
        lines.push(`${t.task_id},"${t.title.replace(/"/g, '""')}",${t.model},${t.status},$${t.cost.toFixed(4)},${t.total_tokens}`);
    }

    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `analytics-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}
