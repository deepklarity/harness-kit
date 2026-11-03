#!/usr/bin/env node
// Scores how usable this workspace's harness is, mechanically. Zero dependencies, read-only
// (the one file it writes is the page you ask for with --html).
// Usage: node check_harness.mjs [--json] [--html [out]] [--threshold N] [--no-gate] [root]
//   The workspace's own declared gate (scripts/verify.sh or a test command in docs/testing_process) is run once
//   with a timeout; --no-gate skips it. Nothing else is ever run except each tool's --help.
//   root defaults to the current folder; --html defaults to writeup/agent-readiness/harness-audit.html.
// Areas: interfaces (what the app exposes), breadcrumbs (coverage x accuracy), tools (CLI parity),
// connections (links between components: imports, HTTP calls to another component's route, queue producers and their tasks, shared tables; each should be named in a breadcrumb),
// index (entry files, then area folders, then leaves: entry rows are areas only, area rows carry the answer or command, every page and script reachable down the tree),
// docs (links, CLAUDE.md), tests (coverage of the interfaces, the declared gate, tiers, assertion strength), quiz (read from writeup/agent-readiness/harness-audit.csv when present).
// Exit 1 when a scored area is below the threshold (default 7). An area that does not apply is "n/a".
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const args = process.argv.slice(2);
const flag = (n) => args.indexOf(n);
const json = flag("--json") >= 0;
let htmlOut = null;
if (flag("--html") >= 0) {
  const nxt = args[flag("--html") + 1];
  htmlOut = nxt && !nxt.startsWith("--") && !existsSync(nxt + "/") ? nxt : null;
  htmlOut ??= "writeup/agent-readiness/harness-audit.html";
}
const thr = flag("--threshold") >= 0 ? Number(args[flag("--threshold") + 1]) : 7;
const noGate = flag("--no-gate") >= 0;
const skipVal = new Set([htmlOut, String(thr)].filter(Boolean));
const rootArg = args.filter((a) => !a.startsWith("--") && !skipVal.has(a))[0];
const root = resolve(rootArg ?? ".");
const here = dirname(fileURLToPath(import.meta.url));

// ---------- walk ----------
const SKIP = new Set(["node_modules", ".git", "venv", ".venv", "env", "dist", "build", "__pycache__", ".stack", "vendor", "site-packages", "docs", "writeup", ".claude", ".verify-logs", ".next", "target", "coverage"]);
const SRC = new Set([".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".php", ".rb", ".go", ".java", ".sql", ".sh", ".json", ".toml", ".cfg"]);
const files = [];
(function walk(d) {
  for (const e of readdirSync(d, { withFileTypes: true })) {
    if (files.length > 20000) return;
    const p = join(d, e.name);
    if (e.isDirectory()) { if (!SKIP.has(e.name) && !e.name.startsWith(".") && !existsSync(join(p, "pyvenv.cfg"))) walk(p); }
    else files.push(p);
  }
})(root);
const rel = (p) => relative(root, p);
const read = (p) => { try { return statSync(p).size < 400_000 ? readFileSync(p, "utf8") : ""; } catch { return ""; } };
const isTest = (p) => /(^|\/)(tests?|__tests__|spec|specs)\//.test(p) || /(^|\/)(test_[^/]+\.py|[^/]+_test\.(py|go)|[^/]+\.(test|spec)\.[cm]?[jt]sx?|[^/]+Test\.php|[^/]+_spec\.rb|conftest\.py)$/.test(p);
const allSrc = files.filter((f) => SRC.has(extname(f))).map((f) => ({ path: rel(f), text: read(f) }));
const testSrc = allSrc.filter((s) => isTest(s.path));
const src = allSrc.filter((s) => !isTest(s.path));
const wordIn = (t, w) => new RegExp(`(^|[^\\w])${w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}([^\\w]|$)`).test(t);
const lineOf = (text, i) => text.slice(0, i).split("\n").length;

// ---------- a. interfaces ----------
const items = [];
const add = (kind, name, file, line, handler) => items.push({ kind, name, file, line, handler });
for (const { path, text } of src) {
  const code = /\.(py|js|mjs|cjs|ts|tsx|jsx|php|rb|go|java)$/.test(path);
  const scan = (re, fn) => { for (const m of text.matchAll(re)) fn(m, lineOf(text, m.index)); };
  if (code) {
    scan(/@\w+\.(?:route|get|post|put|delete|patch)\(\s*["']([^"']+)[^\n]*\n(?:\s*@[^\n]*\n)*\s*(?:async\s+)?def\s+(\w+)/g, (m, l) => add("route", m[1], path, l, m[2]));
    scan(/\b(?:re_)?path\(\s*r?["']([^"']*)["']\s*,/g, (m, l) => /\.py$/.test(path) && add("route", "/" + m[1], path, l));
    scan(/\b(?:app|router|server)\.(get|post|put|delete|patch)\(\s*["'`](\/[^"'`]*)/g, (m, l) => /\.[cm]?[jt]sx?$/.test(path) && add("route", `${m[1].toUpperCase()} ${m[2]}`, path, l));
    scan(/Route::(?:get|post|put|delete|patch|any)\(\s*['"]([^'"]+)/g, (m, l) => add("route", "/" + m[1].replace(/^\//, ""), path, l));
    scan(/\.add_parser\(\s*["']([\w-]+)/g, (m, l) => add("cli", m[1], path, l));
    scan(/@(?:click|app|cli)\.(?:command|group)\([^)]*\)\s*\n\s*(?:async\s+)?def\s+(\w+)/g, (m, l) => add("cli", m[1], path, l));
    scan(/@(?:click\.command|click\.group)\s*\n\s*(?:async\s+)?def\s+(\w+)/g, (m, l) => add("cli", m[1], path, l));
    scan(/\bargparse\.ArgumentParser\(/g, (_m, l) => add("cli", basename(path), path, l));
    scan(/<Route\b[^>]*\bpath=["']([^"']+)/g, (m, l) => add("ui", m[1], path, l));
    scan(/@(?:\w+\.task|shared_task)\b/g, (_m, l) => add("job", basename(path), path, l));
    scan(/\.add_job\(\s*(\w+)/g, (m, l) => add("job", m[1], path, l));
    scan(/\bschedule\.every\(/g, (_m, l) => add("job", basename(path), path, l));
    scan(/\bcron\.schedule\(\s*["']([^"']+)/g, (m, l) => add("job", m[1], path, l));
    scan(/^class\s+(\w+)\((?:[\w.]*\bModel|Base|db\.Model|SQLModel|declarative_base\(\))\)/gm, (m, l) => add("table", m[1], path, l));
    if (/\bst\.set_page_config\(/.test(text)) add("ui", basename(path), path, lineOf(text, text.indexOf("set_page_config")));
  }
  scan(/CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"']?(\w+)/gi, (m, l) => !/^(if|exists|not)$/i.test(m[1]) && add("table", m[1], path, l));
  if (/(^|\/)pages\/[^/]+\.py$/.test(path)) add("ui", basename(path, ".py"), path, 1);
  if (/(^|\/)(jobs|workers|cron)\/[^/]+\.\w+$/.test(path)) add("job", basename(path), path, 1);
}
const pkgPath = join(root, "package.json");
if (existsSync(pkgPath)) {
  try {
    const b = JSON.parse(read(pkgPath)).bin;
    for (const n of typeof b === "string" ? ["package"] : Object.keys(b ?? {})) add("cli", n, "package.json", 1);
  } catch { /* unreadable package.json: nothing to add */ }
}
for (const f of files.filter((f) => /(^|\/)(setup\.py|setup\.cfg|pyproject\.toml)$/.test(rel(f)))) {
  const t = read(f);
  const block = t.match(/(?:console_scripts[^\n]*|\[project\.scripts\])\s*([\s\S]*?)(?:\n\s*\n|\n\[|\]\s*,?\s*\)|$)/);
  for (const m of (block?.[1] ?? "").matchAll(/^\s*["']?([\w.-]+)\s*=\s*["']?[\w.]+:\w+/gm)) add("cli", m[1], rel(f), lineOf(t, t.indexOf(m[0])));
}
const dbFiles = files.filter((f) => /\.(db|sqlite|sqlite3)$/.test(f));
const sqlite = spawnSync("sqlite3", ["-version"], { encoding: "utf8" }).status === 0;
for (const f of dbFiles) {
  if (!sqlite) { add("table", "(sqlite3 not installed, schema unread)", rel(f), 1); continue; }
  const r = spawnSync("sqlite3", ["-readonly", f, ".tables"], { encoding: "utf8", timeout: 3000 });
  for (const t of (r.stdout ?? "").split(/\s+/).filter(Boolean)) add("table", t, rel(f), 1);
}
const seen = new Set();
const inventory = items.filter((i) => { const k = `${i.kind}|${i.name}|${i.file}`; return seen.has(k) ? false : seen.add(k); });
const KINDS = [["route", "HTTP routes"], ["cli", "CLI commands"], ["ui", "UI pages"], ["job", "jobs and workers"], ["table", "SQL tables"]];
const of = (k) => inventory.filter((i) => i.kind === k);

// ---------- b. breadcrumbs ----------
const bcRoot = join(root, "docs/breadcrumb_analysis");
const crumbs = [];
if (existsSync(bcRoot)) {
  const groups = new Map();
  (function walkBc(d) {
    for (const e of readdirSync(d, { withFileTypes: true })) {
      const p = join(d, e.name);
      if (e.isDirectory()) walkBc(p);
      else if (e.name.endsWith(".md") && e.name !== "_INDEX.md" && !(d === bcRoot && e.name.startsWith("_"))) groups.set(d, [...(groups.get(d) ?? []), p]);
    }
  })(bcRoot);
  for (const [d, fs] of groups) {
    if (d === bcRoot) for (const f of fs) crumbs.push({ name: basename(f, ".md"), files: [f] });
    else crumbs.push({ name: relative(bcRoot, d), files: fs });
  }
}
const ignored = read(join(root, ".gitignore")).split("\n").map((l) => l.trim().replace(/^\//, "").replace(/\/$/, "")).filter((l) => l && !l.startsWith("#") && !l.startsWith("!") && !/[*?]/.test(l));
const generated = (p) => p.startsWith(".") || ignored.some((g) => p === g || p.startsWith(g + "/")) || /^(writeup|logs?)\//.test(p);
const srcMap = new Map(src.map((s) => [s.path, s.text]));
const lineCount = (p) => { const t = srcMap.get(p) ?? read(join(root, p)); return t.split("\n").length; };
const PATHRE = /^[\w@.\-/]+\.[A-Za-z0-9]{1,6}$/;
let okChecks = 0, allChecks = 0;
const wrong = [];
for (const c of crumbs) {
  c.text = c.files.map(read).join("\n");
  c.bad = [];
  c.files.forEach((f) => {
    let fence = false;
    read(f).split("\n").forEach((line, i) => {
      if (/^\s*(```|~~~)/.test(line)) fence = !fence;
      const where = `${rel(f)}:${i + 1}`;
      const paths = [];
      for (const m of line.matchAll(/`([^`\s]+)`/g)) {
        const tok = m[1].replace(/[.,;)]+$/, "");
        const lm = tok.match(/^(.+?):(\d+)(?:-(\d+))?$/);
        const p = lm ? lm[1] : tok;
        if (!(p.includes("/") || (PATHRE.test(p) && /\.(py|js|mjs|ts|tsx|jsx|php|rb|go|sh|sql|md|json|ya?ml|toml|html|csv)$/.test(p))) || !PATHRE.test(p) || /^https?:|\{|<|\*|^~/.test(p) || generated(p.replace(/^\.\//, ""))) continue;
        const clean = p.replace(/^\.\//, "");
        allChecks++;
        if (!existsSync(join(root, clean))) { c.bad.push(`${where} cites ${clean}, which does not exist`); continue; }
        okChecks++;
        paths.push(clean);
        if (lm && statSync(join(root, clean)).isFile()) {
          allChecks++;
          const last = Number(lm[3] ?? lm[2]);
          if (last > lineCount(clean)) c.bad.push(`${where} cites ${clean}:${lm[2]}, past its ${lineCount(clean)} lines`); else okChecks++;
        }
      }
      if (!paths.length || fence) return;
      for (const m of line.matchAll(/`([A-Za-z_][\w.]*)(?:\(\))?`/g)) {
        const sym = m[1];
        if (paths.includes(sym) || sym.includes("/") || /\.(py|js|mjs|ts|md|json|sh|csv)$/.test(sym)) continue;
        const texts = paths.filter((p) => /\.(py|js|mjs|cjs|ts|tsx|jsx|php|rb|go|java|sh|sql)$/.test(p)).map((p) => srcMap.get(p) ?? read(join(root, p)));
        if (!texts.length) continue;
        allChecks++;
        const last = sym.split(".").pop();
        if (texts.some((t) => t.includes(last))) okChecks++; else c.bad.push(`${where} names ${sym}, not found in ${paths.join(" or ")}`);
      }
    });
  });
  wrong.push(...c.bad);
}
const cites = (c, i) => {
  const t = c.text;
  if (i.kind !== "route" && i.kind !== "table" && t.includes(i.file)) return true;
  if (i.handler && wordIn(t, i.handler)) return true;
  const n = i.name.replace(/^[A-Z]+ /, "");
  return n.length >= 3 && (i.kind === "route" ? t.includes(n) : wordIn(t, n));
};
const covered = inventory.filter((i) => i.kind !== "cli" || i.name !== basename(i.file) || true).filter((i) => crumbs.some((c) => cites(c, i)));
const uncovered = inventory.filter((i) => !covered.includes(i));
const covShare = inventory.length ? covered.length / inventory.length : 1;
const accShare = allChecks ? okChecks / allChecks : 1;
const bcScore = !inventory.length && !crumbs.length ? null : crumbs.length ? Math.round(covShare * accShare * 100) / 10 : 0;

// ---------- b2. connections: links between components, each of which a breadcrumb should name ----------
// Heuristic and generic: a component is a folder (one or two levels down) with a manifest. Links are imports or path
// references between components, HTTP calls that match a route another component defines, queue producers and the
// consumer that defines the task, and tables one component writes and another reads. Unknown stacks find none.
const MANIFEST = /(^|\/)(package\.json|pyproject\.toml|setup\.py|requirements\.txt|go\.mod|composer\.json|Gemfile|pom\.xml|Cargo\.toml)$/;
const CODE = /\.(py|js|mjs|cjs|ts|tsx|jsx|php|rb|go|java)$/;
const comps = (() => {
  const dirs = new Set();
  for (const f of files) { const parts = rel(f).split("/"); if (MANIFEST.test(rel(f)) && parts.length >= 2 && parts.length <= 3) dirs.add(parts.slice(0, -1).join("/")); }
  if (dirs.size < 2) {
    dirs.clear();
    for (const s of src) if (CODE.test(s.path) && s.path.includes("/")) dirs.add(s.path.split("/")[0]);
    if (dirs.size < 2) dirs.clear();
  }
  return [...dirs].sort();
})();
const compOf = (p) => comps.filter((c) => p.startsWith(c + "/")).sort((a, b) => b.length - a.length)[0] ?? null;
const links = [];
const defs = [];
const addLink = (kind, from, to, a, b, label, also) => {
  if (!from.comp || !to.comp || from.comp === to.comp) return;
  const key = `${kind}|${from.file}|${to.file}|${label}`;
  if (!links.some((l) => l.key === key)) links.push({ key, kind, from, to, a, b, label, also });
};
if (comps.length >= 2) {
  const code = src.filter((s) => CODE.test(s.path) && compOf(s.path));
  const at = (s, m) => lineOf(s.text, m.index);
  const esc = (x) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const here_ = (s) => ({ file: s.path, comp: compOf(s.path) });
  // HTTP: a call whose path matches a route another component defines.
  const segs = (u) => u.replace(/^[a-z][a-z0-9+.-]*:\/\/[^/]*/i, "").replace(/^(?:\$\{[^}]*\}|\{[^}]*\})/, "").split(/[?#]/)[0].replace(/\$\{[^}]*\}|\{[^}]*\}|<[^>]*>|:\w+/g, "*").split("/").filter(Boolean);
  const routes = of("route").map((r) => ({ ...r, comp: compOf(r.file), segs: segs(r.name.replace(/^[A-Z]+ /, "")) }));
  for (const s of code.filter((x) => /\.py$/.test(x.path))) {
    for (const m of s.text.matchAll(/\.register\(\s*r?["']([^"']+)["']/g)) routes.push({ name: "/" + m[1], file: s.path, line: at(s, m), comp: compOf(s.path), segs: segs(m[1]) });
  }
  const CALL = /\b(?:fetch|axios(?:\.(?:get|post|put|patch|delete|request))?|requests\.(?:get|post|put|patch|delete)|httpx\.(?:get|post|put|patch|delete)|(?:http|api|client|session|ky|got)\.(?:get|post|put|patch|delete))\(\s*f?(["'`])([^"'`\n]*)\1/g;
  for (const s of code) for (const m of s.text.matchAll(CALL)) {
    const cs = segs(m[2]);
    if (!cs.some((x) => !x.includes("*"))) continue;
    const fits = (r) => r.segs.some((x) => !x.includes("*")) && r.segs.length <= cs.length && r.segs.every((x, i) => { const y = cs[cs.length - r.segs.length + i]; return x === y || x.includes("*") || y.includes("*"); });
    if (routes.some((r) => r.comp === compOf(s.path) && fits(r))) continue;
    const hit = routes.filter((r) => r.comp !== compOf(s.path) && fits(r)).sort((p, q) => q.segs.length - p.segs.length)[0];
    if (hit) addLink("http", { ...here_(s), line: at(s, m) }, { file: hit.file, line: hit.line, comp: hit.comp }, [s.path], [hit.file, ...(hit.handler ? [{ sym: hit.handler }] : [])], hit.name);
  }
  // Queues: a task producer in one component, the task defined in another.
  for (const s of code) for (const m of s.text.matchAll(/@(?:[\w.]*task|shared_task)\b(\([^)]*\))?[ \t]*\n(?:\s*@[^\n]*\n)*\s*(?:async\s+)?def\s+(\w+)/g)) {
    defs.push({ comp: compOf(s.path), file: s.path, line: lineOf(s.text, m.index + m[0].lastIndexOf("def")), fn: m[2], name: (m[1]?.match(/name\s*=\s*["']([^"']+)/) ?? [])[1] });
  }
  const queueLink = (s, m, d, how) => addLink("task", { ...here_(s), line: at(s, m) }, { file: d.file, line: d.line, comp: d.comp }, [s.path], [d.file, { sym: d.fn }], `${d.fn} via ${how}`);
  for (const s of code) {
    const mine = compOf(s.path);
    for (const m of s.text.matchAll(/\b(\w+)\.(?:delay|apply_async)\(/g)) {
      const d = defs.find((x) => x.fn === m[1] && x.comp !== mine);
      if (d && !defs.some((x) => x.fn === m[1] && x.comp === mine)) queueLink(s, m, d, "delay");
    }
    for (const m of s.text.matchAll(/send_task\(\s*["']([^"']+)["']/g)) {
      const d = defs.find((x) => x.comp !== mine && (x.name === m[1] || x.fn === m[1] || m[1].endsWith("." + x.fn)));
      if (d) queueLink(s, m, d, "send_task");
    }
    for (const m of s.text.matchAll(/\.enqueue(?:_call)?\(\s*["']?([\w.]+)/g)) {
      const fn = m[1].split(".").pop();
      const y = code.filter((x) => compOf(x.path) !== mine).map((x) => ({ x, i: x.text.search(new RegExp(`\\bdef\\s+${esc(fn)}\\s*\\(`)) })).find((z) => z.i >= 0);
      if (y) queueLink(s, m, { file: y.x.path, line: lineOf(y.x.text, y.i), comp: compOf(y.x.path), fn }, "enqueue");
    }
    for (const m of s.text.matchAll(/new\s+Queue\(\s*["'`]([^"'`]+)["'`]/g)) {
      for (const w of code) {
        const wm = [...w.text.matchAll(/new\s+Worker\(\s*["'`]([^"'`]+)["'`]/g)].find((x) => x[1] === m[1]);
        if (wm && compOf(w.path) !== mine) queueLink(s, m, { file: w.path, line: at(w, wm), comp: compOf(w.path), fn: m[1] }, "queue");
      }
    }
  }
  // Imports and path references between components.
  const GENERIC = new Set(["src", "app", "lib", "test", "tests", "docs", "scripts", "core", "utils"]);
  for (const c of comps) {
    const n = c.split("/").pop();
    if (GENERIC.has(n) || n.length < 3) continue;
    const py = new RegExp(`^\\s*(?:from|import)\\s+${esc(n.replace(/-/g, "_"))}(?:[.\\s]|$)`, "m");
    const js = new RegExp(`(?:from\\s+|require\\(\\s*|import\\(\\s*)["']((?:\\.{1,2}/)+|@[\\w.-]+/)${esc(n)}(?:/[^"']*)?["']`, "g");
    const pathRef = new RegExp(`["']((?:\\.\\./)+)${esc(n)}(?:/[^"']*)?["']`, "g");
    for (const s of code) {
      if (compOf(s.path) === c) continue;
      const m0 = /\.py$/.test(s.path) ? s.text.match(py) : null;
      if (m0) addLink("import", { ...here_(s), line: at(s, m0) }, { file: c + "/", line: 1, comp: c }, [s.path], [c + "/"], c);
      for (const m of [...s.text.matchAll(js), ...s.text.matchAll(pathRef)]) {
        const spec = m[0].match(/["']([^"']+)["']/)[1];
        let target = c + "/";
        if (spec.startsWith(".")) {
          const base = join(dirname(s.path), spec);
          if (!(base === c || base.startsWith(c + "/"))) continue;
          const hit = ["", ".ts", ".tsx", ".js", ".mjs", ".py", "/index.ts", "/index.js"].map((e) => base + e).find((x) => existsSync(join(root, x)) && statSync(join(root, x)).isFile());
          if (hit) target = hit;
        }
        addLink("import", { ...here_(s), line: at(s, m) }, { file: target, line: 1, comp: c }, [s.path], [target], c);
      }
    }
  }
  // Tables: written by one component, read by another.
  const SQLSTOP = new Set(["select", "set", "values", "where", "dual", "the", "table"]);
  const writes = [], reads = [];
  for (const s of src.filter((x) => (CODE.test(x.path) || /\.sql$/.test(x.path)) && compOf(x.path))) {
    const w = (m) => writes.push({ t: m[1].toLowerCase(), file: s.path, line: at(s, m), comp: compOf(s.path) });
    for (const m of s.text.matchAll(/\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|REPLACE\s+INTO|DELETE\s+FROM)\s+[`"\[]?(\w+)/gi)) w(m);
    for (const m of s.text.matchAll(/\bUPDATE\s+[`"]?(\w+)[`"]?\s+SET\b/gi)) w(m);
    for (const m of s.text.matchAll(/\b(?:FROM|JOIN)\s+[`"]?([A-Za-z_]\w*)/g)) reads.push({ t: m[1].toLowerCase(), file: s.path, line: at(s, m), comp: compOf(s.path) });
  }
  for (const w of writes) for (const r of reads) if (w.t === r.t && w.comp !== r.comp && w.t.length >= 3 && !SQLSTOP.has(w.t)) addLink("table", w, r, [w.file], [r.file], w.t, [{ sym: w.t }]);
}
const pageHas = (text, tok) => (typeof tok === "string" ? text.includes(tok) : wordIn(text, tok.sym));
for (const l of links) l.covered = crumbs.some((c) => l.a.some((t) => pageHas(c.text, t)) && l.b.some((t) => pageHas(c.text, t)) && (l.also ?? []).every((t) => pageHas(c.text, t)));
const unlinked = links.filter((l) => !l.covered);
const connScore = links.length ? Math.round(((links.length - unlinked.length) / links.length) * 100) / 10 : null;
const linkText = (l) => `${l.kind} ${l.from.file}:${l.from.line} -> ${l.to.file}${l.to.file.endsWith("/") ? "" : ":" + l.to.line} (${l.label})`;

// ---------- c. tools ----------
const tools = [];
const addTool = (name, path, text, run) => tools.push({ name, path, text: text.slice(0, 4000), run });
for (const f of files) {
  const p = rel(f);
  const inDir = /^(scripts|bin)\//.test(p) || /^docs\/.*\/scripts\//.test(p);
  if (inDir && /\.(sh|py|mjs|js|ts|rb)$/.test(p) || (/^(scripts|bin)\//.test(p) && !extname(p))) addTool(p, p, read(f), true);
}
for (const f of ["docs"].flatMap((d) => (existsSync(join(root, d)) ? [] : []))) void f;
(function walkDocs(d) {
  if (!existsSync(d)) return;
  for (const e of readdirSync(d, { withFileTypes: true })) {
    const p = join(d, e.name);
    if (e.isDirectory()) { if (e.name !== "node_modules") walkDocs(p); }
    else if (e.name !== "check_harness.mjs" && /\/scripts\/[^/]*\.(sh|py|mjs|js)$/.test(rel(p)) && !tools.some((t) => t.path === rel(p)) && !/^docs\/(harnesskit|skills)\//.test(rel(p))) addTool(rel(p), rel(p), read(p), true);
  }
})(join(root, "docs"));
for (const mk of ["Makefile", "makefile"]) {
  const t = read(join(root, mk));
  for (const m of t.matchAll(/^([A-Za-z][\w-]*):(?!=)[^\n]*\n((?:\t[^\n]*\n?)*)/gm)) addTool(`make ${m[1]}`, mk, `${m[1]} ${m[2]}`, false);
}
try {
  const sc = JSON.parse(read(pkgPath)).scripts ?? {};
  for (const [k, v] of Object.entries(sc)) addTool(`npm run ${k}`, "package.json", `${k} ${v}`, false);
} catch { /* no package.json scripts */ }
const sandbox = mkdtempSync(join(tmpdir(), "harness-smoke-"));
const RUNNER = { ".sh": "sh", ".py": "python3", ".mjs": "node", ".js": "node", ".rb": "ruby" };
for (const t of tools) {
  if (!t.run) { t.smoke = "listed (not run)"; continue; }
  const full = join(root, t.path);
  if (!/--help|\s-h\b|argparse|click|typer|optparse|usage/i.test(read(full))) { t.smoke = "not run: no --help handling"; continue; }
  const ext = extname(t.path);
  const cmd = RUNNER[ext] ?? (statSync(full).mode & 0o111 ? full : null);
  if (!cmd) { t.smoke = "not run: unknown runner"; continue; }
  const r = spawnSync(cmd, RUNNER[ext] ? [full, "--help"] : ["--help"], { cwd: sandbox, encoding: "utf8", timeout: 2000, env: { PATH: process.env.PATH, HOME: sandbox, NO_PROXY: "*", HTTP_PROXY: "http://127.0.0.1:9", HTTPS_PROXY: "http://127.0.0.1:9" } });
  const out = `${r.stdout ?? ""}${r.stderr ?? ""}`;
  t.smoke = r.error?.code === "ETIMEDOUT" || r.signal ? "failed: timed out" : r.status === 0 || /usage|help|options/i.test(out) ? "ok" : `failed: exit ${r.status}`;
}
const has = (k) => of(k).length > 0;
const webish = has("route") || has("ui") || ["docker-compose.yml", "docker-compose.yaml", "compose.yaml", "Procfile", "Dockerfile"].some((f) => existsSync(join(root, f)));
const JOBS = [
  ["read-only DB query", has("table"), /sqlite|\bselect\b|psql|mysql|\bquery\b|\bsql\b/i, ""],
  ["inspect one record by id", has("table") || has("route"), /inspect|lookup|show|\bget\b|record|by[ _-]?id|\bfind\b/i, ""],
  ["read the logs", true, /\blogs?\b|\btail\b|journalctl/i, ""],
  ["stack up, down, status", webish, /stack|\bstatus\b|\bstart\b|\bstop\b|\bup\b|\bdown\b|serve|compose/i, ""],
  ["call each route", has("route"), /curl|\bcall\b|\brequest|\bapi\b|\broutes?\b|http/i, ""],
].map(([job, applies, re]) => {
  if (!applies) return { job, status: "n/a" };
  const hit = tools.filter((t) => re.test(`${t.name} ${t.text.split("\n").slice(0, 25).join("\n")}`));
  const good = hit.filter((t) => !t.smoke.startsWith("failed"));
  return { job, status: good.length ? "met" : hit.length ? "broken" : "missing", tools: (good.length ? good : hit).map((t) => t.name).slice(0, 4) };
});
const applicable = JOBS.filter((j) => j.status !== "n/a");
const toolScore = applicable.length ? Math.round((applicable.reduce((s, j) => s + (j.status === "met" ? 1 : j.status === "broken" ? 0.5 : 0), 0) / applicable.length) * 100) / 10 : null;
const routeParity = of("route").map((r) => ({ r, hit: tools.some((t) => t.text.includes(r.name.replace(/^[A-Z]+ /, ""))) }));

// ---------- c2. index: entry file -> area index -> leaf, read as folders ----------
// Three entry files: the breadcrumb index (flows), the tools catalogue (scripts) and the docs index (guides).
// The first two list only areas (one line each, no answers); each area folder has its own index whose rows carry the
// answer or command and name the page or script. Every page and entry-point script must be reachable down that tree.
const MAX_ENTRY_ROWS = 12;
const tableRows = (text) => {
  const rows = [], ls = text.split("\n");
  ls.forEach((l, i) => { if (/^\|/.test(l) && !/^\|[\s:|-]+\|?$/.test(l) && !/^\|[\s:|-]+\|?$/.test(ls[i + 1] ?? "")) rows.push(l); });
  return rows;
};
const bulletRows = (text) => text.split("\n").filter((l) => /^\s*[-*]\s/.test(l) && /`|\]\(/.test(l));
const entries = (text) => [...tableRows(text), ...bulletRows(text)];
const PATHLINE = /[\w@.\-/]+\.[A-Za-z0-9]{1,6}:\d+/;
const clauseOf = (row) => row.replace(/`[^`]*`/g, " ").replace(/\[[^\]]*\]\([^)]*\)/g, " ").replace(/[|*_>#-]/g, " ").replace(/\s+/g, " ").trim();
const labelOf = (row) => clauseOf(row).slice(0, 60) || row.slice(0, 60);
const refsOf = (row) => [...row.matchAll(/\]\(([^)\s#]+)[^)]*\)/g)].map((m) => m[1])
  .concat([...row.matchAll(/`([^`]+)`/g)].flatMap((m) => m[1].split(/\s+/)).map((w) => w.replace(/:\d+(?:-\d+)?$/, "")))
  .filter((r) => r && !/^[a-z][a-z0-9+.-]*:/i.test(r) && !r.startsWith("/") && (r.includes("/") || /\.\w{1,5}$/.test(r)));
const resolveRef = (from, r) => [join(dirname(from), r), join(root, r.replace(/^\.\//, ""))].find(existsSync);
const isDir = (p) => { try { return statSync(p).isDirectory(); } catch { return false; } };
const filesUnder = (d) => { const out = []; (function w(x) { for (const e of readdirSync(x, { withFileTypes: true })) { const p = join(x, e.name); if (e.isDirectory()) { if (e.name !== "node_modules" && !e.name.startsWith(".")) w(p); } else out.push(p); } })(d); return out; };
let npmScripts = {};
try { npmScripts = JSON.parse(read(pkgPath)).scripts ?? {}; } catch { /* none */ }
const runnable = (row, idx) => [...row.matchAll(/`([^`]+)`/g)].some((m) => {
  const cmd = m[1].trim();
  const npm = cmd.match(/^npm run (\S+)/);
  if (npm) return npm[1] in npmScripts;
  return cmd.split(/\s+/).filter((w) => /\.(sh|py|mjs|js|ts|rb)$/.test(w) || /^\.?\/?(scripts|bin)\//.test(w)).some((w) => resolveRef(idx, w.replace(/^\.\//, "")));
});

// kind: "pages" (breadcrumbs) or "tools". Returns the rows, problems and everything reached through the tree.
const walkTree = (kind, entryFile, indexName, label) => {
  const t = { label, entryFile, rows: 0, areaRows: 0, subRows: 0, subOk: 0, problems: [], badRows: [], reachedFiles: new Set(), reachedText: "" };
  if (!entryFile) return t;
  const entryRows = entries(read(entryFile));
  t.rows = entryRows.length;
  const queue = [];
  for (const row of entryRows) {
    let idx = null, other = null;
    for (const r of refsOf(row)) {
      const p = resolveRef(entryFile, r);
      if (!p) continue;
      const d = isDir(p) ? p : basename(p) === indexName ? dirname(p) : null;
      if (d && d !== dirname(entryFile) && existsSync(join(d, indexName))) { idx = join(d, indexName); break; }
      other ??= p;
    }
    if (idx && !PATHLINE.test(row)) { t.areaRows++; queue.push(idx); }
    else if (idx) t.problems.push(`entry row carries path:line, which belongs in the area index: ${labelOf(row)}`);
    else if (other) t.problems.push(`leaf row in the entry file: ${labelOf(row)}`);
    else t.problems.push(`entry row points at no area folder: ${labelOf(row)}`);
  }
  if (t.rows > MAX_ENTRY_ROWS) t.problems.push(`${label} entry file has ${t.rows} rows, keep it to ${MAX_ENTRY_ROWS}`);
  const seenIdx = new Set();
  while (queue.length) {
    const idx = queue.shift();
    if (seenIdx.has(idx)) continue;
    seenIdx.add(idx);
    t.reachedFiles.add(idx);
    for (const row of entries(read(idx))) {
      t.subRows++;
      t.reachedText += row + "\n";
      let hit = false;
      for (const r of refsOf(row)) {
        const p = resolveRef(idx, r);
        if (!p) continue;
        if (isDir(p) && existsSync(join(p, indexName))) { queue.push(join(p, indexName)); hit = true; }
        else if (isDir(p)) { filesUnder(p).forEach((f) => t.reachedFiles.add(f)); hit = true; }
        else { t.reachedFiles.add(p); if (kind === "pages") hit = true; }
      }
      const ok = kind === "pages" ? hit && clauseOf(row).split(/\s+/).filter(Boolean).length >= 3 : hit || runnable(row, idx);
      const cmdOk = kind === "tools" ? hit || runnable(row, idx) : true;
      if (ok && cmdOk) t.subOk++; else t.badRows.push(kind === "pages" ? `area row lacks an answer or a page: ${labelOf(row)}` : `area row has no runnable command: ${labelOf(row)}`);
    }
  }
  // Folders next to the entry file that hold leaves but no index of their own.
  for (const e of readdirSync(dirname(entryFile), { withFileTypes: true })) {
    const d = join(dirname(entryFile), e.name);
    if (e.isDirectory() && !e.name.startsWith(".") && e.name !== "node_modules" && !existsSync(join(d, indexName)) && filesUnder(d).some((f) => (kind === "pages" ? f.endsWith(".md") : true))) t.problems.push(`area without an index: ${rel(d)}`);
  }
  return t;
};
const bcEntry = join(bcRoot, "_INDEX.md");
const CATALOGUES = ["scripts/README.md", "docs/testing_process/diagnostics.md", "docs/testing_process/diagnostic_scripts.md"];
const catFile = CATALOGUES.map((c) => join(root, c)).find(existsSync);
const KIT_SCRIPTS = new Set(["check_claude_md.mjs", "check_doc_links.mjs", "check_harness.mjs", "clean_run.sh", "verify.sh"]);
// Only entry points count: a script someone runs (shebang, __main__ block or executable bit), not a module it imports.
const entryPoint = (t) => {
  if (basename(t.path).startsWith("_") || /\.(gitignore|md|txt|json|ya?ml|toml|cfg)$/.test(t.path)) return false;
  const text = read(join(root, t.path));
  let exec = false;
  try { exec = (statSync(join(root, t.path)).mode & 0o111) !== 0; } catch {}
  return exec || text.startsWith("#!") || /__name__\s*==\s*["']__main__["']/.test(text);
};
const scriptTools = tools.filter((t) => t.run && !KIT_SCRIPTS.has(basename(t.path)) && entryPoint(t));
const bcTree = walkTree("pages", existsSync(bcEntry) ? bcEntry : null, "_INDEX.md", "breadcrumb");
const toolTree = walkTree("tools", catFile ?? null, "README.md", "tools");
const pages = crumbs.flatMap((c) => c.files.map(rel));
const unreachablePages = pages.filter((p) => !bcTree.reachedFiles.has(join(root, p)));
const unreachableTools = scriptTools.filter((s) => !toolTree.reachedFiles.has(join(root, s.path)) && !s.path.split("/").length ? false : !toolTree.reachedFiles.has(join(root, s.path)) && !toolTree.reachedText.includes(s.path) && !wordIn(toolTree.reachedText, basename(s.path))).map((s) => s.path);
const unreachable = [...unreachablePages, ...unreachableTools];
const docsEntry = join(root, "docs/_INDEX.md");
const docsRows = existsSync(docsEntry) ? entries(read(docsEntry)) : [];
const docsOk = docsRows.filter((r) => refsOf(r).some((x) => resolveRef(docsEntry, x)));
const share = (a, b) => (b ? a / b : 0);
const parts = [];
for (const [t, apply] of [[bcTree, crumbs.length || bcTree.entryFile], [toolTree, scriptTools.length || catFile]]) {
  if (!apply) continue;
  parts.push(share(t.areaRows, t.rows) * (t.rows > MAX_ENTRY_ROWS ? MAX_ENTRY_ROWS / t.rows : 1));
  parts.push(share(t.subOk, t.subRows));
}
if (pages.length + scriptTools.length) parts.push(1 - unreachable.length / (pages.length + scriptTools.length));
if (docsRows.length) parts.push(docsOk.length / docsRows.length);
const indexScore = parts.length ? Math.round((parts.reduce((s, x) => s + x, 0) / parts.length) * 100) / 10 : null;
const treeFull = (t, what) => [
  `${t.areaRows} of ${t.rows} ${t.label} entry rows are areas with an index${t.entryFile ? ` (${rel(t.entryFile)})` : " (no entry file)"}`,
  `${t.subOk} of ${t.subRows} ${t.label} area index rows ${what}`,
  ...t.problems, ...t.badRows,
];
const indexProblems = [...bcTree.problems, ...toolTree.problems];
const indexFull = [
  ...treeFull(bcTree, "carry an answer and a page"),
  ...treeFull(toolTree, "have a runnable command"),
  ...(docsRows.length ? [`${docsOk.length} of ${docsRows.length} docs index rows point at an existing path`, ...docsRows.filter((r) => !docsOk.includes(r)).map((r) => `docs index row points at nothing: ${labelOf(r)}`)] : []),
  `unreachable: ${unreachable.length ? unreachable.join(", ") : "none"}`,
];

// Per-component jobs: a component with a database or a queue needs an inspect tool and a logs tool in the catalogue tree.
const compJobs = comps.flatMap((c) => {
  const n = c.split("/").pop();
  const own = src.filter((s) => s.path.startsWith(c + "/")).map((s) => s.text).join("\n") + files.filter((f) => rel(f).startsWith(c + "/") && MANIFEST.test(rel(f))).map(read).join("\n");
  const db = of("table").some((i) => i.file.startsWith(c + "/")) || /\b(sqlalchemy|django\.db|psycopg2?|sqlite3|mysql|pymongo|prisma|typeorm|sequelize|knex|mongoose|peewee|flask_sqlalchemy)\b/i.test(own);
  const queue = defs.some((d) => d.comp === c) || /\b(celery|bullmq|dramatiq|sidekiq|new Queue\(|\brq\b)/i.test(own);
  const mine = scriptTools.filter((t) => (toolTree.reachedFiles.has(join(root, t.path)) || toolTree.reachedText.includes(t.path)) && (t.path.startsWith(c + "/") || `${t.path} ${t.text}`.toLowerCase().includes(n.toLowerCase())));
  return [[`inspect ${n} data`, db || queue, /inspect|lookup|show|\bget\b|record|by[ _-]?id|\bfind\b|sqlite|\bselect\b|psql|mysql|\bquery\b|\bsql\b|queue|celery|flower/i], [`read ${n} logs`, db || queue, /\blogs?\b|\btail\b|journalctl/i]].map(([job, applies, re]) => {
    if (!applies) return { job, status: "n/a" };
    const hit = mine.filter((t) => re.test(`${t.name} ${t.text.split("\n").slice(0, 25).join("\n")}`));
    const good = hit.filter((t) => !t.smoke.startsWith("failed"));
    return { job, status: good.length ? "met" : hit.length ? "broken" : "missing", tools: (good.length ? good : hit).map((t) => t.name).slice(0, 4) };
  });
});
const allJobs = [...JOBS, ...compJobs];
const jobsLive = allJobs.filter((j) => j.status !== "n/a");
const toolScoreAll = jobsLive.length ? Math.round((jobsLive.reduce((s, j) => s + (j.status === "met" ? 1 : j.status === "broken" ? 0.5 : 0), 0) / jobsLive.length) * 100) / 10 : null;

// ---------- d. docs ----------
const sib = (n) => join(here, n);
const linkRun = existsSync(sib("check_doc_links.mjs")) ? spawnSync("node", [sib("check_doc_links.mjs"), root], { encoding: "utf8" }) : null;
const cmdRun = existsSync(sib("check_claude_md.mjs")) && existsSync(join(root, "CLAUDE.md")) ? spawnSync("node", [sib("check_claude_md.mjs"), join(root, "CLAUDE.md")], { encoding: "utf8", cwd: root }) : null;
const docGaps = [];
let linkScore = 10, mdScore = 10;
if (linkRun) {
  const bad = (linkRun.stdout.match(/broken link/g) ?? []).length - (linkRun.status ? 1 : 0);
  if (linkRun.status) { linkScore = Math.max(0, 10 - Math.max(bad, 1)); docGaps.push(...linkRun.stdout.trim().split("\n").slice(0, -1)); }
} else docGaps.push("check_doc_links.mjs not found beside this script");
if (cmdRun) {
  const m = cmdRun.stdout.match(/passed (\d+) of (\d+) checks/);
  mdScore = m ? (10 * Number(m[1])) / Number(m[2]) : 0;
  if (cmdRun.status) docGaps.push(...cmdRun.stdout.trim().split("\n").filter((l) => !/^passed/.test(l)).map((l) => `CLAUDE.md: ${l}`));
} else { mdScore = 0; docGaps.push("CLAUDE.md missing"); }
const docsScore = Math.round(Math.min(linkScore, mdScore) * 10) / 10;
const commands = [];
const tp = join(root, "docs/testing_process");
if (existsSync(tp)) for (const n of readdirSync(tp).filter((n) => n.endsWith(".md"))) {
  let fence = false;
  for (const l of read(join(tp, n)).split("\n")) {
    if (/^\s*(```|~~~)/.test(l)) { fence = !fence; continue; }
    if (fence && l.trim() && !l.trim().startsWith("#")) commands.push(`${n}: ${l.trim()}`);
  }
}

// ---------- e. tests ----------
const testFiles = testSrc.filter((t) => !t.path.endsWith("conftest.py") && /\.(py|js|mjs|cjs|ts|tsx|jsx|php|rb|go)$/.test(t.path));
const pkgText = read(pkgPath);
const runners = [
  [/pytest/.test(pkgText) || existsSync(join(root, "pytest.ini")) || /\[tool\.pytest|\[tool:pytest/.test(read(join(root, "pyproject.toml")) + read(join(root, "setup.cfg"))) || testFiles.some((t) => /\.py$/.test(t.path) && /\bpytest\b/.test(t.text)) || files.some((f) => basename(f) === "conftest.py") || testFiles.some((t) => /\.py$/.test(t.path) && /^\s*def test_/m.test(t.text) && !/unittest/.test(t.text)), "pytest"],
  [testFiles.some((t) => /import unittest|from unittest/.test(t.text)), "unittest"],
  [/"jest"/.test(pkgText), "jest"], [/"vitest"/.test(pkgText), "vitest"],
  [testFiles.some((t) => t.path.endsWith("_test.go")), "go test"],
  [existsSync(join(root, "phpunit.xml")) || existsSync(join(root, "phpunit.xml.dist")), "phpunit"],
  [existsSync(join(root, ".rspec")) || testFiles.some((t) => t.path.endsWith("_spec.rb")), "rspec"],
].filter(([ok]) => ok).map(([, n]) => n);
const stem = (i) => i.kind === "route" ? i.name.replace(/^[A-Z]+ /, "").split(/[<:{]/)[0].replace(/\/$/, "") : i.name;
const exercised = (i) => testFiles.some((t) => {
  const s = stem(i), mod = basename(i.file).replace(/\.[^.]+$/, "");
  return (s.length >= 3 && (i.kind === "route" ? t.text.includes(s) : wordIn(t.text, s))) || (i.handler && wordIn(t.text, i.handler)) || (i.kind !== "table" && i.kind !== "route" && mod.length >= 3 && wordIn(t.text, mod));
});
const untested = inventory.filter((i) => !(i.kind === "table" && i.name.startsWith("(")) && !exercised(i));
const testedShare = inventory.length ? 1 - untested.length / inventory.length : 1;
// the gate: only the workspace's own declared command, never built from guesses
const GATE = /^(\.\/)?(scripts\/verify\.sh|pytest|python3? -m pytest|npm (run )?test|make test|go test|vendor\/bin\/phpunit|bundle exec rspec)\b[^;&|><$`]*$/;
// A big repo's full gate can take many minutes; profile.md `audit_gate:` names the fast tier to run instead.
const profileGate = (() => {
  try { return /^audit_gate:\s*(.+)$/m.exec(readFileSync(join(root, "docs/harnesskit/profile.md"), "utf8"))?.[1].trim() ?? null; } catch { return null; }
})();
let gateCmd = profileGate ?? (existsSync(join(root, "scripts/verify.sh")) ? "scripts/verify.sh" : null);
if (!gateCmd) gateCmd = commands.map((c) => c.replace(/^[^:]*:\s*/, "").replace(/^\$\s*/, "")).find((c) => GATE.test(c)) ?? null;
const gate = { cmd: gateCmd, state: gateCmd ? "not run" : "none declared", detail: "" };
if (gateCmd && !noGate) {
  const [bin, ...a] = gateCmd.split(/\s+/);
  const r = spawnSync(bin.includes("/") ? join(root, bin) : bin, a, { cwd: root, encoding: "utf8", timeout: 120_000, maxBuffer: 20e6 });
  const out = `${r.stdout ?? ""}${r.stderr ?? ""}`;
  const n = (re) => Number(out.match(re)?.[1] ?? NaN);
  const passed = n(/(\d+) passed/), failed = n(/(\d+) failed/);
  gate.state = r.error?.code === "ETIMEDOUT" ? "timed out" : r.error ? `failed: ${r.error.code ?? "error"}` : r.status === 0 ? "pass" : `fail: exit ${r.status}`;
  if (gate.state === "timed out") gate.detail = "over 120s: set audit_gate in docs/harnesskit/profile.md to the fast tier (for example scripts/verify.sh unit)";
  gate.detail = [isNaN(passed) ? "" : `${passed} passed`, isNaN(failed) ? "" : `${failed} failed`].filter(Boolean).join(", ");
}
const LIVE = /(^|\/)(live|e2e|stack|integration|smoke|slow)(\/|_|\.)|@pytest\.mark\.(live|slow|integration|e2e|stack)|\blive\b/i;
const liveFiles = testFiles.filter((t) => LIVE.test(t.path) || LIVE.test(t.text.slice(0, 3000)));
const fastFiles = testFiles.filter((t) => !liveFiles.includes(t));
const cases = [];
for (const t of testFiles) {
  if (/\.py$/.test(t.path)) {
    const parts = t.text.split(/^(?=[ \t]*(?:async\s+)?def test_)/m).slice(1);
    for (const b of parts) cases.push({ file: t.path, name: b.match(/def (test_\w+)/)[1], body: b.split(/^(?=\S)/m)[0], py: true });
  } else if (/\.(js|mjs|cjs|ts|tsx|jsx)$/.test(t.path)) {
    const parts = t.text.split(/(?=\b(?:it|test)(?:\.\w+)?\(\s*["'`])/).slice(1);
    for (const b of parts) cases.push({ file: t.path, name: b.match(/\(\s*["'`]([^"'`]*)/)[1], body: b, py: false });
  }
}
const hasAssert = (c) => (c.py ? /\bassert\b|self\.assert|pytest\.raises|\.should\b/ : /\bexpect\(|\bassert\b|\.should\b/).test(c.body);
const weakLine = (c, l) => c.py ? /^\s*assert\s+(not\s+)?[\w.\[\]()'"]+\s*$/.test(l) || /^\s*assert\s+["'][^"']*["']\s+in\b/.test(l) || /self\.assert(True|IsNotNone)\(/.test(l) : /toBeTruthy|toBeDefined|toBeFalsy|toContain\(\s*["'`]/.test(l);
const asserted = (c, l) => c.py ? /^\s*assert\b|self\.assert|pytest\.raises/.test(l) : /\bexpect\(|\bassert\b/.test(l);
const noAssert = cases.filter((c) => !hasAssert(c));
const weak = cases.filter((c) => { const ls = c.body.split("\n").filter((l) => asserted(c, l)); return ls.length && ls.every((l) => weakLine(c, l)); });
const strongShare = cases.length ? 1 - (noAssert.length + weak.length) / cases.length : 0;
const unitTier = fastFiles.length > 0;
const testScore = !testFiles.length ? 0 : Math.round((4 * testedShare + 3 * (gate.state === "pass" ? 1 : gate.state === "not run" || gate.state === "none declared" || gate.state === "timed out" ? 0.5 : 0) + 2 * strongShare + (unitTier ? 1 : 0)) * 10) / 10;
const testGaps = !testFiles.length ? ["no tests"] : [
  ...(untested.length ? [`${untested.length} of ${inventory.length} interfaces untested: ${untested.slice(0, 3).map((i) => i.name).join(", ")}`] : []),
  ...(gate.state.startsWith("fail") ? [`gate ${gate.cmd}: ${gate.state}`] : gate.state === "timed out" ? [`gate ${gate.cmd} ${gate.detail}`] : gate.state === "none declared" ? ["no declared test gate"] : []),
  ...(noAssert.length ? [`${noAssert.length} of ${cases.length} tests assert nothing`] : []),
  ...(weak.length ? [`${weak.length} tests assert only truthiness or string presence`] : []),
  ...(!unitTier ? ["every test needs the live stack"] : []),
];

// ---------- quiz (written by the setup agent, see docs/adoption/HARNESS_AUDIT.md) ----------
const csv = read(join(root, "writeup/agent-readiness/harness-audit.csv")).split("\n").slice(1).filter(Boolean).map((l) => l.split(","));
const quizRows = csv.filter((r) => r[0] === "quiz" && !isNaN(Number(r[2])));
const quizScore = quizRows.length ? Math.round((quizRows.reduce((s, r) => s + Number(r[2]), 0) / quizRows.length) * 10) / 10 : null;

// ---------- report ----------
const inv = KINDS.map(([k, label]) => `${of(k).length} ${label}`).join(", ");
const areas = [
  { area: "interfaces", score: null, gaps: [`found: ${inv}`, ...KINDS.filter(([k]) => !has(k)).map(([, l]) => `${l}: not detected`)], full: inventory.map((i) => `${i.kind} ${i.name} (${i.file}:${i.line})`) },
  { area: "breadcrumbs", score: bcScore, gaps: [...(crumbs.length ? [] : ["no breadcrumbs under docs/breadcrumb_analysis"]), ...(uncovered.length ? [`${uncovered.length} of ${inventory.length} interfaces uncovered: ${uncovered.slice(0, 3).map((i) => i.name).join(", ")}`] : []), ...wrong.slice(0, 3)], full: [`coverage ${covered.length}/${inventory.length}, citations ${okChecks}/${allChecks} correct`, ...uncovered.map((i) => `uncovered: ${i.kind} ${i.name} (${i.file}:${i.line})`), ...wrong] },
  { area: "connections", score: connScore, gaps: links.length ? (unlinked.length ? [`${unlinked.length} of ${links.length} links uncovered: ${unlinked.slice(0, 2).map(linkText).join("; ")}`] : []) : [comps.length < 2 ? "one component: not applicable" : "no cross-component links detected"], full: [`${comps.length} components: ${comps.join(", ") || "none"}`, `coverage ${links.length - unlinked.length}/${links.length}`, ...links.map((l) => `${l.covered ? "covered" : "uncovered"}: ${linkText(l)}`)] },
  { area: "index", score: indexScore, gaps: [...indexProblems.slice(0, 3), ...(indexScore !== null && indexScore < 7 && unreachable.length ? [`unreachable: ${unreachable.slice(0, 6).join(", ")}${unreachable.length > 6 ? ", ..." : ""}`] : [])], full: indexFull },
  { area: "tools", score: toolScoreAll, gaps: allJobs.filter((j) => j.status === "missing" || j.status === "broken").map((j) => `${j.job}: ${j.status}`), full: [...allJobs.map((j) => `${j.job}: ${j.status}${j.tools?.length ? " via " + j.tools.join(", ") : ""}`), ...tools.map((t) => `${t.name}: ${t.smoke}`), ...routeParity.map((p) => `route ${p.r.name}: ${p.hit ? "a tool mentions it" : "no tool mentions it"}`)] },
  { area: "docs", score: docsScore, gaps: docGaps.slice(0, 3), full: [...docGaps, ...commands.map((c) => `to run: ${c}`)] },
  { area: "tests", score: testScore, gaps: testGaps, full: [`runner: ${runners.join(", ") || "not detected"}; ${testFiles.length} test files, ${cases.length} test functions`, `gate: ${gate.cmd ?? "none"}: ${gate.state}${gate.detail ? " (" + gate.detail + ")" : ""}`, `tiers: ${fastFiles.length} unit or mock files, ${liveFiles.length} live or stack files`, ...untested.map((i) => `untested: ${i.kind} ${i.name} (${i.file}:${i.line})`), ...noAssert.map((c) => `no assertion: ${c.file} ${c.name}`), ...weak.map((c) => `weak assertion: ${c.file} ${c.name}`)] },
  { area: "quiz", score: quizScore, gaps: quizScore === null ? ["not run: see docs/adoption/HARNESS_AUDIT.md"] : quizRows.filter((r) => Number(r[2]) < 7).map((r) => `${r[1]}: ${r.slice(3).join(",")}`), full: quizRows.map((r) => r.join(",")) },
];
for (const a of areas) a.state = a.score === null ? "n/a" : a.score >= thr ? "pass" : "fail";
const fail = areas.some((a) => a.state === "fail");

if (htmlOut) {
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const rows = areas.map((a) => `<tbody class="item"><tr class="main" tabindex="0"><td class="chev">&#9656;</td><td class="id">${a.area}</td><td class="num ${a.state}">${a.score === null ? "n/a" : a.score + "/10"}</td><td>${esc(a.gaps.slice(0, 2).join("; ") || "no gaps")}</td></tr><tr class="more"><td></td><td colspan="3"><ul>${a.full.map((x) => `<li>${esc(x)}</li>`).join("") || "<li>nothing to list</li>"}</ul></td></tr></tbody>`).join("");
  const page = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Harness Audit</title><style>
:root{color-scheme:light}
body{margin:0;background:#f4f5f7;color:#1c1f23;font:15px/1.55 'Manrope',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}
main{max-width:940px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:26px;margin:0 0 6px}
.verdict{font-size:18px;font-weight:600;margin:0 0 24px}
section{background:#fff;border:1px solid #dcdfe4;border-radius:10px;padding:20px;margin-bottom:18px}
.label{font:600 11px/1 'JetBrains Mono',ui-monospace,'SF Mono',Menlo,monospace;letter-spacing:.08em;text-transform:uppercase;color:#4a5560;margin:0 0 12px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #e8eaee;vertical-align:top}
th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#5b6570}
.id,.num{font-family:'JetBrains Mono',ui-monospace,'SF Mono',Menlo,monospace;font-size:13px;white-space:nowrap}
.num.pass{color:#1d6b3a}.num.fail{color:#9c2a22}.num.n\\/a{color:#5b6570}
tbody.item>tr.main{cursor:pointer}tbody.item>tr.main:hover td{background:#f7f8fa}
tbody.item>tr.more{display:none}tbody.item.open>tr.more{display:table-row}
tbody.item.open .chev{transform:rotate(90deg)}.chev{color:#5b6570;width:16px}
tr.more td{background:#fafbfc;font-size:13px}tr.more ul{margin:4px 0;padding-left:20px}
.muted{color:#5b6570;font-size:13px}
:focus-visible{outline:2px solid #2f6feb;outline-offset:2px}
@media (max-width:640px){main{padding:20px 16px 48px}}
</style></head><body><main>
<h1>Harness audit</h1>
<p class="verdict">${fail ? "Below " + thr + ": " + areas.filter((a) => a.state === "fail").map((a) => a.area).join(", ") : "Every scored area is " + thr + " or better"}</p>
<section><p class="label">Areas</p><table><thead><tr><th></th><th>Area</th><th>Score</th><th>Top gaps</th></tr></thead>${rows}</table>
<p class="muted">Rows open to the full list. Score is out of 10; the bar is ${thr}. Run from ${esc(basename(root))} by check_harness.mjs.</p></section>
</main><script>document.querySelectorAll("tbody.item>tr.main").forEach(function(r){var t=function(){r.parentNode.classList.toggle("open")};r.addEventListener("click",t);r.addEventListener("keydown",function(e){if(e.key==="Enter"||e.key===" "){e.preventDefault();t()}})})</script></body></html>
`;
  const out = resolve(root, htmlOut);
  mkdirSync(dirname(out), { recursive: true });
  writeFileSync(out, page);
}

if (json) console.log(JSON.stringify({ root, threshold: thr, pass: !fail, areas, inventory, tools: tools.map(({ name, path, smoke }) => ({ name, path, smoke })), jobs: allJobs }, null, 2));
else {
  for (const a of areas) console.log(`${a.area.padEnd(12)} ${(a.score === null ? "n/a" : a.score + "/10").padEnd(7)} ${a.gaps.slice(0, 3).join("; ") || "no gaps"}`);
  console.log(fail ? `below ${thr}: ${areas.filter((a) => a.state === "fail").map((a) => a.area).join(", ")}` : `every scored area is ${thr} or better`);
}
process.exitCode = fail ? 1 : 0;
