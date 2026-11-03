import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { findHits, main } from "../src/check-generic.js";

const TERMS = ["acme-internal", "widgetco", "DeepKlarity"];

const SAMPLE = `# Skill

Run your tests.

## Example: in harness-kit

Run \`cd acme-internal && make test\`.

Elsewhere, for example widgetco/backend, also leaks.
`;

describe("check-generic strict mode", () => {
  let dir: string;
  let path: string;
  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "cg-"));
    path = join(dir, "SKILL.md");
    writeFileSync(path, SAMPLE);
  });
  afterEach(() => rmSync(dir, { recursive: true, force: true }));

  it("honours example markers in default mode", () => {
    expect(findHits(path, "SKILL.md", { strict: false, terms: TERMS })).toEqual([]);
  });

  it("scans example sections and example lines in strict mode", () => {
    const hits = findHits(path, "SKILL.md", { strict: true, terms: TERMS });
    expect(hits).toEqual([[7, "acme-internal"], [9, "widgetco"]]);
  });

  it("allows the published package name but still flags the org name elsewhere", () => {
    const f = join(dir, "pkg.md");
    writeFileSync(f, "npx @deepklarity/harnesskit init\nbuilt at DeepKlarity\n");
    expect(findHits(f, "pkg.md", { strict: true, terms: TERMS })).toEqual([[2, "DeepKlarity"]]);
  });

  it("returns no hits for an unreadable file", () => {
    expect(findHits(join(dir, "missing.md"), "missing.md", { strict: true, terms: TERMS })).toEqual([]);
  });

  it("runs over the real kit with --strict and finds it clean", () => {
    const out: string[] = [];
    vi.spyOn(process.stdout, "write").mockImplementation((s) => (out.push(String(s)), true));
    const code = main(["--strict"]);
    vi.restoreAllMocks();
    expect(out.join("")).toMatch(/^generic: clean/);
    expect(code).toBe(0);
  });
});
