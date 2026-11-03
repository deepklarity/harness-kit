import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { readManifest, sha256, writeManifest } from "../src/manifest.js";

const tmp = () => mkdtempSync(join(tmpdir(), "hk-manifest-"));

describe("manifest", () => {
  it("hashes like sha256 of the bytes", () => {
    expect(sha256("abc")).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  });

  it("returns null when the workspace has no manifest", () => {
    expect(readManifest(tmp())).toBeNull();
  });

  it("writes the version line first, then sorted 'sha  path' lines, and reads them back", () => {
    const t = tmp();
    const a = sha256("a");
    const b = sha256("b");
    writeManifest(t, { version: "1.2.3", hashes: new Map([["docs/z.md", b], ["docs/a.md", a]]) });
    const text = readFileSync(join(t, "docs/harnesskit/manifest"), "utf8");
    expect(text).toBe(`version 1.2.3\n${a}  docs/a.md\n${b}  docs/z.md\n`);
    const m = readManifest(t)!;
    expect(m.version).toBe("1.2.3");
    expect(m.hashes.get("docs/a.md")).toBe(a);
    expect(m.hashes.size).toBe(2);
  });

  it("keeps paths with spaces", () => {
    const t = tmp();
    writeManifest(t, { version: "1", hashes: new Map([["docs/a b.md", sha256("x")]]) });
    expect(readManifest(t)!.hashes.has("docs/a b.md")).toBe(true);
  });

  it("rejects a manifest without a version line, naming the first line", () => {
    const t = tmp();
    mkdirSync(join(t, "docs/harnesskit"), { recursive: true });
    writeFileSync(join(t, "docs/harnesskit/manifest"), "oops\n");
    expect(() => readManifest(t)).toThrow(/oops/);
  });

  it("rejects a malformed hash line, naming it", () => {
    const t = tmp();
    mkdirSync(join(t, "docs/harnesskit"), { recursive: true });
    writeFileSync(join(t, "docs/harnesskit/manifest"), "version 1\nnothash  docs/a.md\n");
    expect(() => readManifest(t)).toThrow(/nothash/);
  });
});

describe("manifest baseline_commit", () => {
  it("round-trips a sha and null, and reads an old manifest as undefined", () => {
    const t = tmp();
    writeManifest(t, { version: "1", hashes: new Map(), baselineCommit: "abc123" });
    expect(readFileSync(join(t, "docs/harnesskit/manifest"), "utf8")).toBe("version 1\nbaseline_commit abc123\n");
    expect(readManifest(t)!.baselineCommit).toBe("abc123");
    writeManifest(t, { version: "1", hashes: new Map(), baselineCommit: null });
    expect(readManifest(t)!.baselineCommit).toBeNull();
    writeManifest(t, { version: "1", hashes: new Map() });
    expect(readManifest(t)!.baselineCommit).toBeUndefined();
  });
});

describe("manifest nested_baseline", () => {
  it("round-trips one line per nested repo, sorted, and reads none from an older manifest", () => {
    const t = mkdtempSync(join(tmpdir(), "hk-nb-"));
    const nestedBaselines = new Map([["libs/web", "b".repeat(40)], ["api", "a".repeat(40)]]);
    writeManifest(t, { version: "1", hashes: new Map(), baselineCommit: "abc123", nestedBaselines });
    expect(readFileSync(join(t, "docs/harnesskit/manifest"), "utf8")).toBe(`version 1\nbaseline_commit abc123\nnested_baseline api ${"a".repeat(40)}\nnested_baseline libs/web ${"b".repeat(40)}\n`);
    expect([...readManifest(t)!.nestedBaselines!]).toEqual([["api", "a".repeat(40)], ["libs/web", "b".repeat(40)]]);
    writeManifest(t, { version: "1", hashes: new Map() });
    expect(readManifest(t)!.nestedBaselines!.size).toBe(0);
  });
});
