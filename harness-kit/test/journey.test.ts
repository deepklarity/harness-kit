// Consent journey (writeup/release-readiness/journey-spec.md): init writes reference material only;
// every workflow file changes through `harnesskit apply <step>`. Black box through the CLI.
// Assumption pinned here: apply takes `--target DIR` like init does.
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { chmodSync, cpSync, rmSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { main } from "../src/cli.js";
import { runProve } from "../src/prove.js";

const git = (d: string, ...a: string[]) => execFileSync("git", ["-C", d, ...a], { encoding: "utf8" });
const commit = (d: string, msg: string) => git(d, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", msg);

/** stdout and stderr together: the person sees both. */
async function cli(argv: string[]) {
  const out: string[] = [];
  const o = process.stdout.write;
  const e = process.stderr.write;
  process.stdout.write = ((s: string) => (out.push(String(s)), true)) as typeof process.stdout.write;
  process.stderr.write = ((s: string) => (out.push(String(s)), true)) as typeof process.stderr.write;
  try {
    return { code: await main(argv), out: out.join("") };
  } finally {
    process.stdout.write = o;
    process.stderr.write = e;
  }
}

const init = (d: string) => cli(["init", "--target", d]);

const fresh = () => {
  const d = realpathSync(mkdtempSync(join(tmpdir(), "hk-journey-")));
  execFileSync("git", ["init", "-q", d]);
  commit(d, "base");
  return d;
};

const SETTINGS = `{
    "model": "opus",
    "permissions": { "allow": ["Bash(ls)"] },
    "hooks": {
        "PreToolUse": [ { "matcher": "Bash", "hooks": [ { "type": "command", "command": "echo mine" } ] } ],
        "Stop": [ { "hooks": [ { "type": "command", "command": "echo bye" } ] } ]
    }
}
`;

/** A workspace that already has every workflow file the kit could touch. */
const established = () => {
  const d = fresh();
  const put = (p: string, s: string) => (mkdirSync(join(d, p, ".."), { recursive: true }), writeFileSync(join(d, p), s));
  put("CLAUDE.md", "# Ours\n\nOur rules.\n");
  put("AGENTS.md", "theirs\n");
  put(".claude/settings.json", SETTINGS);
  put(".claude/skills/mine/SKILL.md", "---\nname: mine\n---\nmine\n");
  put(".gitignore", "node_modules");
  git(d, "add", "-A");
  commit(d, "ours");
  return d;
};

/** path -> sha1 of every file outside .git. */
function tree(d: string): Record<string, string> {
  const out: Record<string, string> = {};
  const walk = (dir: string) => {
    for (const n of readdirSync(dir)) {
      if (n === ".git") continue;
      const p = join(dir, n);
      if (statSync(p).isDirectory()) walk(p);
      else out[relative(d, p)] = createHash("sha1").update(readFileSync(p)).digest("hex");
    }
  };
  walk(d);
  return out;
}
const porcelain = (d: string) =>
  git(d, "status", "--porcelain", "--untracked-files=all").split("\n").filter(Boolean).map((l) => l.replace(/^\s*\S+\s+/, "")).sort();
const read = (d: string, p: string) => readFileSync(join(d, p), "utf8");

const WORKFLOW = ["CLAUDE.md", "AGENTS.md", ".gitignore", ".claude/settings.json", ".claude/hooks/guard.mjs", ".claude/hooks/protected.txt"];
const csv = readFileSync(join(__dirname, "../kit/skills/skills.csv"), "utf8").trim().split("\n").slice(1).map((l) => l.split(","));
const kitSkills = (cat?: string) => csv.filter((r) => r[2] === "kit" && (!cat || r[1] === cat)).map((r) => r[0]).sort();
const stubs = (d: string) => (existsSync(join(d, ".claude/skills")) ? readdirSync(join(d, ".claude/skills")).sort() : []);

describe("1. init writes reference paths only", () => {
  it("a fresh repo gains only docs/ and writeup/ paths, and .harnesskit/ is excluded locally", async () => {
    const d = fresh();
    const r = await init(d);
    expect(r.code).toBe(0);
    const paths = porcelain(d);
    expect(paths.length).toBeGreaterThan(10);
    expect(paths.filter((p) => !/^(docs|writeup)\//.test(p))).toEqual([]);
    expect(paths).toContain("docs/harnesskit/manifest");
    expect(paths).toContain("writeup/agent-readiness/board.csv");
    for (const p of WORKFLOW) expect(existsSync(join(d, p)), p).toBe(false);
    expect(existsSync(join(d, ".claude"))).toBe(false);
    expect(read(d, ".git/info/exclude").split("\n")).toContain(".harnesskit/");
  });

  it("keeps the exclude line single across re-runs and does not remove other exclude lines", async () => {
    const d = fresh();
    writeFileSync(join(d, ".git/info/exclude"), "*.swp\n");
    await init(d);
    await init(d);
    const lines = read(d, ".git/info/exclude").split("\n");
    expect(lines.filter((l) => l === ".harnesskit/")).toHaveLength(1);
    expect(lines).toContain("*.swp");
  });
});

describe("2. init leaves a workspace's own workflow files byte-identical", () => {
  it("CLAUDE.md, AGENTS.md, settings.json, own skill and .gitignore are untouched", async () => {
    const d = established();
    const before = tree(d);
    const r = await init(d);
    expect(r.code).toBe(0);
    const after = tree(d);
    for (const p of Object.keys(before)) expect(after[p], p).toBe(before[p]);
    expect(read(d, ".claude/settings.json")).toBe(SETTINGS);
    expect(read(d, ".gitignore")).toBe("node_modules");
    expect(readdirSync(join(d, ".claude"))).toEqual(["settings.json", "skills"]);
    expect(stubs(d)).toEqual(["mine"]);
    expect(Object.keys(after).filter((p) => !(p in before)).filter((p) => !/^(docs|writeup)\//.test(p))).toEqual([]);
  });
});

describe("6. init output", () => {
  it("counts per top folder, says nothing else changed, and ends with the setup prompt", async () => {
    const d = fresh();
    const r = await init(d);
    expect(r.out).toMatch(/docs\/?[^\n]*\d+|\d+[^\n]*docs\//);
    expect(r.out).toMatch(/writeup\/?[^\n]*\d+|\d+[^\n]*writeup\//);
    const nothing = "Nothing else changed. Setup will show you each change to CLAUDE.md, AGENTS.md, .claude/ and .gitignore before making it.";
    const next = "Next, hand the repo to your agent:";
    const prompt = 'claude "Set up this repo with harnesskit - follow docs/harnesskit/entry.md"';
    expect(r.out).toContain(nothing);
    expect(r.out).toContain(next);
    expect(r.out).toContain(prompt);
    expect(r.out.indexOf(nothing)).toBeLessThan(r.out.indexOf(next));
    expect(r.out.indexOf(next)).toBeLessThan(r.out.indexOf(prompt));
    expect(r.out.trimEnd().split("\n").length).toBeLessThan(24);
    expect(r.out).toContain("✓ 1. Install");
    expect(r.out).toContain("○ 7. Prove it");
  });

  it("names no workflow file as written (the fixed sentence aside)", async () => {
    const d = fresh();
    const r = await init(d);
    const rest = r.out
      .replace(/Nothing else changed\.[^\n]*/, "")
      .replace(/\S*docs\/\S*/g, "")
      .replace(/^\s*claude "[^\n]*$/m, "");
    expect(rest).not.toMatch(/CLAUDE\.md|AGENTS\.md|\.claude\/|settings\.json|guard\.mjs|\.gitignore/);
    expect(rest).not.toMatch(/unfilled slots?/);
  });
});

describe("3. docs/harnesskit/workflow/ holds ready-made copies", () => {
  const W = "docs/harnesskit/workflow";
  it("one stub per kit-tier skill, byte-equal to the goldens", async () => {
    const d = fresh();
    await init(d);
    const golden = join(__dirname, "fixtures/golden/stubs");
    const want = readdirSync(golden).sort();
    expect(want.length).toBeGreaterThan(10);
    expect(readdirSync(join(d, W, ".claude/skills")).sort()).toEqual(want);
    for (const n of want) expect(read(d, `${W}/.claude/skills/${n}/SKILL.md`), n).toBe(readFileSync(join(golden, n, "SKILL.md"), "utf8"));
  });

  it("the guard hook, an empty protected.txt, a valid settings.hook.json, AGENTS.md and gitignore.lines", async () => {
    const d = fresh();
    await init(d);
    expect(read(d, `${W}/.claude/hooks/guard.mjs`)).toBe(readFileSync(join(__dirname, "../kit/templates/hooks/guard.mjs"), "utf8"));
    expect(read(d, `${W}/.claude/hooks/protected.txt`)).toBe("");
    const hook = JSON.parse(read(d, `${W}/.claude/settings.hook.json`));
    const text = JSON.stringify(hook);
    expect(text).toContain("PreToolUse");
    expect(text).toContain("guard.mjs");
    expect(read(d, `${W}/AGENTS.md`)).toContain("CLAUDE.md");
    expect(read(d, `${W}/gitignore.lines`).split("\n").filter(Boolean)).toEqual(["writeup/", ".harnesskit/"]);
  });

  it("the copies are reference files: the manifest knows them and the tree under the workflow paths is untouched", async () => {
    const d = fresh();
    await init(d);
    expect(read(d, "docs/harnesskit/manifest")).toContain(`${W}/gitignore.lines`);
    expect(existsSync(join(d, ".claude"))).toBe(false);
  });
});

describe("8. prove's clean-tree check", () => {
  let root: string, oldPath: string;
  beforeAll(() => {
    root = realpathSync(mkdtempSync(join(tmpdir(), "hk-journey-prove-")));
    mkdirSync(join(root, "bin"));
    writeFileSync(
      join(root, "bin/claude"),
      `#!/usr/bin/env node\nconsole.log(JSON.stringify({ type: "result", subtype: "success", total_cost_usd: 0.5, num_turns: 1, duration_ms: 1000, result: "done", usage: { input_tokens: 1, output_tokens: 1 } }));\n`,
    );
    chmodSync(join(root, "bin/claude"), 0o755);
    oldPath = process.env.PATH ?? "";
    process.env.PATH = `${join(root, "bin")}:${oldPath}`;
  });
  afterAll(() => {
    process.env.PATH = oldPath;
  });

  it("passes with an untracked writeup/ and no .gitignore anywhere", async () => {
    const d = fresh();
    await init(d);
    git(d, "add", "docs");
    commit(d, "kit docs");
    expect(existsSync(join(d, ".gitignore"))).toBe(false);
    expect(porcelain(d).every((p) => p.startsWith("writeup/"))).toBe(true);
    expect(porcelain(d).length).toBeGreaterThan(0);

    const scenario = join(root, "ok.md");
    writeFileSync(scenario, "# Ok\n\nTier: mid\nDifficulty: 2\nKind: outcome\nBills: yes\nExercises: x\n\n## The prompt\n\nSay hi.\n\n## The proof\n\n```sh\ntrue\n```\n\n## A correct answer must mention\n\n- done\n");
    const out: string[] = [];
    const code = await runProve([scenario, "--runner", "headless", "--budget", "10"], { cwd: d, log: (s) => out.push(s) });
    expect(out.join("\n")).not.toMatch(/uncommitted/);
    expect(code).toBe(0);
    expect(existsSync(join(d, ".gitignore"))).toBe(false);
  });
});

describe("4 and 9. re-running init after the person installed and edited workflow files", () => {
  const W = "docs/harnesskit/workflow";
  const install = (d: string) => {
    cpSync(join(d, W, ".claude"), join(d, ".claude"), { recursive: true });
    cpSync(join(d, W, "AGENTS.md"), join(d, "AGENTS.md"));
    cpSync(join(d, W, "gitignore.lines"), join(d, ".gitignore"));
    cpSync(join(d, "docs/harnesskit/templates/CLAUDE.md"), join(d, "CLAUDE.md"));
  };
  const outside = (t: Record<string, string>) => Object.fromEntries(Object.entries(t).filter(([p]) => !/^(docs|writeup)\//.test(p)));

  it("init does not touch, revert or recreate any workflow file", async () => {
    const d = fresh();
    await init(d);
    install(d);
    writeFileSync(join(d, "CLAUDE.md"), "# Mine now\n");
    writeFileSync(join(d, ".claude/hooks/guard.mjs"), "// mine\n");
    writeFileSync(join(d, ".claude/hooks/protected.txt"), "api\n");
    writeFileSync(join(d, ".gitignore"), "mine\n");
    rmSync(join(d, ".claude/skills/skill-audit"), { recursive: true });
    rmSync(join(d, "AGENTS.md"));
    const before = outside(tree(d));
    expect((await init(d)).code).toBe(0);
    expect(outside(tree(d))).toEqual(before);
    expect(existsSync(join(d, "AGENTS.md"))).toBe(false);
    expect(existsSync(join(d, ".claude/skills/skill-audit"))).toBe(false);
    expect(read(d, "CLAUDE.md")).toBe("# Mine now\n");
  });

  it("--check exits 0 and does not report workflow files, edited or not", async () => {
    const d = fresh();
    await init(d);
    install(d);
    writeFileSync(join(d, ".claude/hooks/guard.mjs"), "// mine\n");
    writeFileSync(join(d, "CLAUDE.md"), "# Mine now\n{{unfilled}}\n");
    const r = await cli(["init", "--check", "--target", d]);
    expect(r.code).toBe(0);
    expect(r.out).not.toMatch(/\.claude\/|AGENTS\.md|\.gitignore|settings\.json|(^|[^/\w])CLAUDE\.md|unfilled/);
  });

  it("a person's edit to a docs/harnesskit/workflow copy is drift and is left alone", async () => {
    const d = fresh();
    await init(d);
    writeFileSync(join(d, W, "gitignore.lines"), "mine\n");
    const r = await init(d);
    expect(r.out).toContain(`${W}/gitignore.lines`);
    expect(read(d, `${W}/gitignore.lines`)).toBe("mine\n");
  });
});

describe("7. kit/entry.md asks before it installs", () => {
  const entry = readFileSync(join(__dirname, "../kit/entry.md"), "utf8");
  const chunkAround = (re: RegExp) => {
    const m = re.exec(entry);
    expect(m, `entry.md never mentions ${re}`).not.toBeNull();
    const start = entry.lastIndexOf("\n#", m!.index) + 1;
    const next = entry.indexOf("\n#", m!.index);
    return entry.slice(start, next === -1 ? undefined : next);
  };
  const steps: [string, RegExp][] = [
    ["CLAUDE.md", /diff[^\n]*CLAUDE\.md|CLAUDE\.md[^\n]*diff/i],
    ["AGENTS.md", /workflow\/AGENTS\.md/],
    [".gitignore", /gitignore\.lines/],
    ["guard hook", /settings\.hook\.json/],
    ["skills", /workflow\/\.claude\/skills/],
  ];
  for (const [name, re] of steps) {
    it(`the ${name} step shows and asks before it copies or merges`, () => {
      const chunk = chunkAround(re);
      const show = chunk.search(/\bshow/i);
      const ask = chunk.search(/\bask|AskUserQuestion/i);
      const copy = chunk.search(/\bcop(y|ies|ied)\b|\bmerge/i);
      expect(show, "no show").toBeGreaterThanOrEqual(0);
      expect(ask, "no ask").toBeGreaterThanOrEqual(0);
      if (copy >= 0) {
        expect(show).toBeLessThan(copy);
        expect(ask).toBeLessThan(copy);
      }
    });
  }

  it("points at docs/harnesskit/workflow/, no longer claims init wrote workflow files, and uses no apply command", () => {
    expect(entry).toContain("docs/harnesskit/workflow/");
    expect(entry).not.toMatch(/harnesskit apply|Init already copied[^.]*(guard hook|`AGENTS\.md`)|It also added\s+`writeup\/`/);
  });
});
