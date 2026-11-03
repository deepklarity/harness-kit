import { spawnSync } from "node:child_process";
import { appendFileSync, cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));
const script = join(here, "../kit/templates/scripts/check_harness.mjs");
const workspace = (remove: string[] = []) => {
  const ws = mkdtempSync(join(tmpdir(), "harness-"));
  cpSync(join(here, "fixtures/harness/app"), ws, { recursive: true });
  for (const r of remove) rmSync(join(ws, r), { recursive: true, force: true });
  return ws;
};
const run = (ws: string, ...a: string[]) => spawnSync("node", [script, ...a, ws], { encoding: "utf8" });
const report = (ws: string, ...a: string[]) => JSON.parse(run(ws, "--json", ...a).stdout);
const area = (r: any, n: string) => r.areas.find((x: any) => x.area === n);

describe("check_harness", () => {
  it("inventories routes, tables and commands", () => {
    const r = report(workspace(), "--no-gate");
    const names = r.inventory.map((i: any) => `${i.kind}:${i.name}`);
    expect(names).toContain("route:/users/<int:uid>");
    expect(names).toContain("route:/reports/weekly");
    expect(names).toContain("table:users");
    expect(names).toContain("cli:dbq.py");
    expect(area(r, "interfaces").gaps.join()).toContain("UI pages: not detected");
  });

  it("covers the cited route, lists the uncovered one, and catches the wrong citation", () => {
    const b = area(report(workspace(), "--no-gate"), "breadcrumbs");
    expect(b.full.join("\n")).toContain("uncovered: route /reports/weekly");
    expect(b.full.join("\n")).not.toContain("uncovered: route /users");
    expect(b.full.join("\n")).toContain("legacy/users.py, which does not exist");
    expect(b.full.join("\n")).toContain("names load_user, not found in");
    expect(b.full.join("\n")).not.toContain("app.py:7, past");
    expect(b.score).toBeGreaterThan(0);
    expect(b.score).toBeLessThan(7);
  });

  it("smoke-runs a tool with --help and leaves one without help handling alone", () => {
    const ws = workspace();
    const r = report(ws, "--no-gate");
    const t = Object.fromEntries(r.tools.map((x: any) => [x.name, x.smoke]));
    expect(t["scripts/dbq.py"]).toBe("ok");
    expect(t["scripts/risky.sh"]).toContain("not run");
    expect(existsSync(join(ws, "marker.txt"))).toBe(false);
  });

  it("marks jobs that do not apply as n/a and names the missing ones", () => {
    const r = report(workspace(), "--no-gate");
    const jobs = Object.fromEntries(r.jobs.map((j: any) => [j.job, j.status]));
    expect(jobs["read-only DB query"]).toBe("met");
    expect(jobs["read the logs"]).toBe("missing");
    const bare = report(workspace(["app.py", "schema.sql", "scripts", "tests"]), "--no-gate");
    const bj = Object.fromEntries(bare.jobs.map((j: any) => [j.job, j.status]));
    expect(bj["read-only DB query"]).toBe("n/a");
    expect(bj["call each route"]).toBe("n/a");
  });

  it("scores tests: one route tested, one not, weak and empty tests flagged", () => {
    const t = area(report(workspace(), "--no-gate"), "tests");
    const text = t.full.join("\n");
    expect(text).toContain("runner: pytest");
    expect(text).toContain("untested: route /reports/weekly");
    expect(text).not.toContain("untested: route /users");
    expect(text).toContain("no assertion: tests/test_app.py test_runs");
    expect(text).toContain("weak assertion: tests/test_app.py test_weak");
    expect(t.gaps[0]).toContain("untested");
  });

  it("runs only the declared gate and records its result", () => {
    expect(area(report(workspace()), "tests").full.join("\n")).toContain("scripts/verify.sh: pass (3 passed)");
    expect(area(report(workspace(), "--no-gate"), "tests").full.join("\n")).toContain("scripts/verify.sh: not run");
  });

  it("runs the fast tier the profile names as audit_gate instead of the full gate", () => {
    const ws = workspace();
    mkdirSync(join(ws, "docs/harnesskit"), { recursive: true });
    writeFileSync(join(ws, "docs/harnesskit/profile.md"), "# Harness profile\n\naudit_gate: scripts/verify.sh unit\n");
    expect(area(report(ws), "tests").full.join("\n")).toContain("scripts/verify.sh unit: pass");
  });

  it("scores a workspace with no tests 0 with the gap no tests", () => {
    const t = area(report(workspace(["tests"]), "--no-gate"), "tests");
    expect(t.score).toBe(0);
    expect(t.gaps).toEqual(["no tests"]);
  });

  it("exits 1 when an area is below the bar, 0 when the bar is lowered, prints one line per area", () => {
    const ws = workspace();
    const r = run(ws, "--no-gate");
    expect(r.status).toBe(1);
    expect(r.stdout.split("\n").filter((l) => /^(interfaces|breadcrumbs|connections|index|tools|docs|tests|quiz) /.test(l))).toHaveLength(8);
    expect(run(ws, "--no-gate", "--threshold", "0").status).toBe(0);
  });

  it("reads quiz scores from the audit csv", () => {
    const ws = workspace();
    cpSync(join(here, "fixtures/harness/harness-audit.csv"), join(ws, "writeup/agent-readiness/harness-audit.csv"));
    const q = area(report(ws, "--no-gate"), "quiz");
    expect(q.score).toBe(6);
    expect(q.gaps[0]).toContain("users");
  });

  it("writes a self-contained page, and nothing else", () => {
    const ws = workspace();
    const out = join(ws, "page.html");
    run(ws, "--no-gate", "--html", out);
    const html = readFileSync(out, "utf8");
    expect(html).toContain("<title>Harness Audit</title>");
    expect(html).toContain("breadcrumbs");
    expect(html).not.toMatch(/src="http|href="http/);
  });
  const fixture = (name: string) => {
    const ws = mkdtempSync(join(tmpdir(), "harness-"));
    cpSync(join(here, "fixtures", name), ws, { recursive: true });
    return ws;
  };
  const put = (ws: string, rel: string, text: string) => { mkdirSync(dirname(join(ws, rel)), { recursive: true }); writeFileSync(join(ws, rel), text); };

  describe("index area (entry -> area -> leaf)", () => {
    it("reaches every page and script through entry, area index and leaf; lists the orphan page", () => {
      const a = area(report(fixture("harness-index/tree"), "--no-gate"), "index");
      const text = a.full.join("\n");
      expect(text).toContain("unreachable: docs/breadcrumb_analysis/billing/orphan.md");
      expect(text).not.toContain("users/login");
      expect(text).not.toContain("scripts/db/query.py,");
      expect(text).toContain("2 of 2 breadcrumb entry rows are areas with an index");
      expect(text).toContain("2 of 2 tools entry rows are areas with an index");
      expect(a.score).toBeLessThan(10);
      expect(a.score).toBeGreaterThanOrEqual(7);
    });

    it("scores 10 once the orphan page is gone", () => {
      const ws = fixture("harness-index/tree");
      rmSync(join(ws, "docs/breadcrumb_analysis/billing/orphan.md"));
      const a = area(report(ws, "--no-gate"), "index");
      expect(a.score).toBe(10);
      expect(a.full.join("\n")).toContain("unreachable: none");
    });

    it("flags leaf rows in the entry file and the pages that hang off them", () => {
      const a = area(report(fixture("harness-index/flat"), "--no-gate"), "index");
      const text = a.full.join("\n");
      expect(text).toContain("leaf row in the entry file: How does login work?");
      expect(text).toContain("leaf row in the entry file: Query the database");
      expect(text).toContain("area without an index: docs/breadcrumb_analysis/users");
      expect(a.score).toBeLessThan(7);
      expect(a.gaps.join("\n")).toContain("leaf row");
    });

    it("flags an entry row whose area folder has no index, and an entry file over twelve rows", () => {
      const ws = fixture("harness-index/tree");
      rmSync(join(ws, "docs/breadcrumb_analysis/billing/_INDEX.md"));
      const many = Array.from({ length: 14 }, (_, i) => `| [users](users/_INDEX.md) | area ${i} |`).join("\n");
      put(ws, "scripts/README.md", `# Tools\n\n| Area | Covers |\n|---|---|\n${many}\n`);
      const text = area(report(ws, "--no-gate"), "index").full.join("\n");
      expect(text).toContain("area without an index: docs/breadcrumb_analysis/billing");
      expect(text).toContain("tools entry file has 14 rows, keep it to 12");
    });

    it("counts an area index row whose script does not exist as not runnable", () => {
      const ws = fixture("harness-index/tree");
      put(ws, "scripts/db/README.md", "# Database tools\n\n| Task | Command |\n|---|---|\n| Query | `python3 scripts/db/query.py --help` prints rows |\n| Ghost | `python3 scripts/db/ghost.py` prints nothing |\n");
      expect(area(report(ws, "--no-gate"), "index").full.join("\n")).toContain("area row has no runnable command: Ghost");
    });
  });

  describe("connections area", () => {
    it("detects the route call and the task enqueue, covers the cited one, lists the other", () => {
      const a = area(report(fixture("harness-connections"), "--no-gate"), "connections");
      const text = a.full.join("\n");
      expect(text).toContain("2 components: client, service");
      expect(text).toContain("covered: http client/main.py:8 -> service/app.py:6");
      expect(text).toContain("uncovered: task client/main.py:12 -> service/tasks.py:5 (process_order via send_task)");
      expect(a.score).toBe(5);
      expect(a.gaps.join("\n")).toContain("1 of 2 links uncovered");
    });

    it("is n/a with one component and never fails", () => {
      const a = area(report(workspace(), "--no-gate"), "connections");
      expect(a.score).toBeNull();
      expect(a.state).toBe("n/a");
    });

    it("covers the task link once a page names both ends", () => {
      const ws = fixture("harness-connections");
      appendFileSync(join(ws, "docs/breadcrumb_analysis/orders/FLOW.md"), "\n`client/main.py:12` sends `process_order`, defined in `service/tasks.py:5`.\n");
      expect(area(report(ws, "--no-gate"), "connections").score).toBe(10);
    });

    it("asks for an inspect tool and a logs tool for each component that has a database or queue", () => {
      const t = area(report(fixture("harness-connections"), "--no-gate"), "tools");
      expect(t.gaps.join("\n")).toContain("inspect service data: missing");
      expect(t.gaps.join("\n")).toContain("read service logs: missing");
    });

    it("meets the per-component jobs with a catalogued tool", () => {
      const ws = fixture("harness-connections");
      put(ws, "scripts/README.md", "# Tools\n\n| Area | Covers |\n|---|---|\n| [service](service/README.md) | Inspect service data and logs |\n");
      put(ws, "scripts/service/README.md", "# Service tools\n\n| Task | Command |\n|---|---|\n| Inspect a service record, select rows | `python3 scripts/service/inspect.py --help` |\n| Tail the service logs | `sh scripts/service/logs.sh` |\n");
      put(ws, "scripts/service/inspect.py", "#!/usr/bin/env python3\n# select one service record by id\nimport argparse\n");
      put(ws, "scripts/service/logs.sh", "#!/bin/sh\n# tail the service logs\n");
      const t = area(report(ws, "--no-gate"), "tools");
      expect(t.gaps.join("\n")).not.toContain("service data");
      expect(t.gaps.join("\n")).not.toContain("service logs");
      expect(t.full.join("\n")).toContain("inspect service data: met");
    });
  });
});
