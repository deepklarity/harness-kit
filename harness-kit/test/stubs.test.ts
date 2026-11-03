import { describe, expect, it } from "vitest";
import { mkdtempSync, readdirSync, readFileSync, cpSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { generateStubs } from "../src/stubs.js";

const skills = "kit/skills";
const golden = "test/fixtures/golden/stubs";

describe("generateStubs", () => {
  it("writes every kit-tier stub byte-equal to the goldens and nothing else", () => {
    const out = mkdtempSync(join(tmpdir(), "stubs-"));
    generateStubs(skills, out, { tiers: ["kit"] });
    const want = readdirSync(golden).sort();
    expect(readdirSync(out).sort()).toEqual(want);
    for (const name of want) {
      expect(readFileSync(join(out, name, "SKILL.md"), "utf8")).toBe(
        readFileSync(join(golden, name, "SKILL.md"), "utf8"),
      );
    }
  });

  it("defaults to the kit tier", () => {
    const out = mkdtempSync(join(tmpdir(), "stubs-"));
    generateStubs(skills, out);
    expect(readdirSync(out).sort()).toEqual(readdirSync(golden).sort());
  });

  it("skips skills whose tier was not asked for", () => {
    const out = mkdtempSync(join(tmpdir(), "stubs-"));
    generateStubs(skills, out, { tiers: ["workspace"] });
    expect(readdirSync(out)).toEqual([]);
  });

  it("raises a clear error naming a skill listed in the csv but missing on disk", () => {
    const copy = mkdtempSync(join(tmpdir(), "kit-"));
    cpSync(skills, copy, { recursive: true });
    rmSync(join(copy, "audits/debuggability-audit"), { recursive: true });
    const out = mkdtempSync(join(tmpdir(), "stubs-"));
    expect(() => generateStubs(copy, out)).toThrow(/debuggability-audit.*audits\/debuggability-audit\/SKILL\.md/);
  });

  it("raises when a body has no frontmatter", () => {
    const copy = mkdtempSync(join(tmpdir(), "kit-"));
    cpSync(skills, copy, { recursive: true });
    writeFileSync(join(copy, "audits/debuggability-audit/SKILL.md"), "no frontmatter\n");
    expect(() => generateStubs(copy, mkdtempSync(join(tmpdir(), "stubs-")))).toThrow(/frontmatter/);
  });
});
