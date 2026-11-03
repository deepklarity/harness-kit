import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
// @ts-expect-error plain .mjs with no types
import { checkSurface } from "../kit/skills/audits/hk-surface-design/scripts/check_surface.mjs";

const SCRIPT = resolve("kit/skills/audits/hk-surface-design/scripts/check_surface.mjs");
type Finding = { item: string; line: number; message: string };
const run = (text: string): Finding[] => checkSurface(text);
const messages = (text: string) => run(text).map((f) => f.message).join("\n");

const CLEAN = `<!doctype html>
<html lang="en">
<head><meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
:root{--bg:#fbfaf7}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#16150f}}
.kicker{text-transform:uppercase}
@media (max-width:640px){td{display:block}}
</style></head>
<body><p class="eyebrow">rendered 14:02 from board.csv</p>
<table><tr><th>#</th><th>What</th><th>Status</th><th>Owner</th><th>When</th><th>Why</th></tr>
<tr><td>1</td><td>x</td><td><span class="pill good">done</span></td><td>a</td><td>b</td><td>c</td></tr></table>
</body></html>`;

describe("check_surface.mjs", () => {
  it("passes a page that follows the rules", () => {
    expect(run(CLEAN)).toEqual([]);
  });

  it("flags a hardcoded light theme and a missing dark block, with the line", () => {
    const page = CLEAN.replace('<html lang="en">', '<html lang="en" data-theme="light">').replace(/@media \(prefers-color-scheme[^\n]*\n/, "");
    const f = run(page);
    expect(f.find((x) => /hardcoded/.test(x.message))?.line).toBe(2);
    expect(messages(page)).toMatch(/no @media \(prefers-color-scheme: dark\)/);
  });

  it("flags a missing viewport meta", () => {
    expect(messages(CLEAN.replace(/<meta name="viewport"[^>]*>/, ""))).toMatch(/no viewport meta/);
  });

  it("flags status shown by colour alone but not a pill with a word", () => {
    expect(messages(CLEAN)).not.toMatch(/colour alone/);
    expect(messages(CLEAN.replace(">done</span>", "></span>"))).toMatch(/empty \.pill: status shown by colour alone/);
  });

  it("flags uppercase overuse and a long phrase set in uppercase", () => {
    const rules = ".a{text-transform:uppercase}.b{text-transform:uppercase}.c{text-transform:uppercase}\n.pill{text-transform:uppercase}";
    const page = CLEAN.replace("</style>", `${rules}</style>`).replace(">done<", ">after what is the spend cap for a session<");
    const m = messages(page);
    expect(m).toMatch(/5 rules set uppercase/);
    expect(m).toMatch(/\.pill sets a 9-word phrase in uppercase/);
    expect(messages(CLEAN)).not.toMatch(/uppercase/);
  });

  it("flags a wide table only when there is no narrow layout", () => {
    expect(messages(CLEAN.replace(/@media \(max-width[^\n]*\n/, ""))).toMatch(/table with 6 columns and no @media \(max-width\)/);
    expect(messages(CLEAN)).not.toMatch(/columns/);
  });

  it("flags unfilled tokens and a missing stamp", () => {
    const page = CLEAN.replace("rendered 14:02 from board.csv", "{{headline}}");
    const m = messages(page);
    expect(m).toMatch(/unfilled token \{\{headline\}\}/);
    expect(m).toMatch(/no last-updated stamp/);
  });

  it("reads markdown: wide tables, tokens, stamp", () => {
    const md = "# Board\n\n| a | b | c | d | e | f |\n|---|---|---|---|---|---|\n| 1 | 2 | 3 | 4 | 5 | 6 |\n\n{{next}}\n";
    const items = run(md).map((f) => f.item).sort();
    expect(items).toEqual(["13-filled", "13-narrow", "2-fresh"]);
    expect(run("Generated 2026-10-13 from board.csv\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")).toEqual([]);
  });

  it("exits 1 with located lines when flagged, 0 when clean", () => {
    const dir = mkdtempSync(join(tmpdir(), "surface-"));
    writeFileSync(join(dir, "ok.html"), CLEAN);
    writeFileSync(join(dir, "bad.md"), "{{x}}\n");
    expect(execFileSync("node", [SCRIPT, join(dir, "ok.html")], { encoding: "utf8" })).toMatch(/static pass clean/);
    const bad = spawnSync("node", [SCRIPT, join(dir, "bad.md")], { encoding: "utf8" });
    expect(bad.status).toBe(1);
    expect(bad.stdout).toMatch(/13-filled\s+\S+bad\.md:1\s+unfilled token/);
  });
});
