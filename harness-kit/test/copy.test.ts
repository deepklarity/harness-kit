import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import { check, copyKit } from "../src/copy.js";
import { readManifest, sha256 } from "../src/manifest.js";

let kit: string;
let ws: string;

const put = (root: string, rel: string, text: string) => {
  mkdirSync(dirname(join(root, rel)), { recursive: true });
  writeFileSync(join(root, rel), text);
};
const read = (root: string, rel: string) => readFileSync(join(root, rel), "utf8");

beforeEach(() => {
  const root = mkdtempSync(join(tmpdir(), "hk-copy-"));
  kit = join(root, "kit");
  ws = join(root, "ws");
  mkdirSync(ws);
  put(kit, "skills/foo/hk-foo/SKILL.md", "---\nname: hk-foo\n---\n\n# Foo\n");
  put(kit, "skills/foo/hk-foo/REFERENCE.md", "ref\n");
  put(kit, "skills/bar/hk-bar/SKILL.md", "---\nname: hk-bar\n---\n\n# Bar\n");
  put(kit, "skills/skills.csv", "name,category,tier,reason\nhk-foo,foo,kit,\nhk-bar,bar,workspace,depends on this workspace\n");
  put(kit, "docs/_INDEX.md", "# map\n");
  put(kit, "docs/playbook/tenets.md", "# Tenets\n\nBe good.\n");
  put(kit, "entry.md", "# Entry\n");
  put(kit, "templates/mission-control/board.csv", "id,status\n");
  put(kit, "templates/mission-control/RESUME.md", "# Resume\n");
});

describe("copyKit first run", () => {
  it("copies docs, kit-tier skills, entry and the mission-control template; skips workspace-tier skills", () => {
    const r = copyKit(kit, ws, { version: "1.0.0" });
    expect(read(ws, "docs/_INDEX.md")).toBe("# map\n");
    expect(read(ws, "docs/playbook/tenets.md")).toContain("Be good.");
    expect(read(ws, "docs/skills/foo/hk-foo/SKILL.md")).toContain("# Foo");
    expect(read(ws, "docs/skills/foo/hk-foo/REFERENCE.md")).toBe("ref\n");
    expect(read(ws, "docs/harnesskit/entry.md")).toBe("# Entry\n");
    expect(read(ws, "docs/harnesskit/templates/mission-control/board.csv")).toBe("id,status\n");
    expect(existsSync(join(ws, "docs/skills/bar"))).toBe(false);
    expect(r.written).toContain("docs/skills/foo/hk-foo/SKILL.md");
    expect(r.upgraded).toEqual([]);
    expect(r.drifted).toEqual([]);
  });

  it("does not create docs/org", () => {
    copyKit(kit, ws, { version: "1.0.0" });
    expect(existsSync(join(ws, ".claude"))).toBe(false);
    expect(existsSync(join(ws, "docs/org"))).toBe(false);
  });

  it("writes a manifest with the version and the hash of every written file, none for workspace-tier", () => {
    const r = copyKit(kit, ws, { version: "1.0.0" });
    const m = readManifest(ws)!;
    expect(m.version).toBe("1.0.0");
    expect(m.hashes.get("docs/skills/foo/hk-foo/SKILL.md")).toBe(sha256(read(ws, "docs/skills/foo/hk-foo/SKILL.md")));
    expect([...m.hashes.keys()].some((k) => k.includes("hk-bar"))).toBe(false);
    expect(m.hashes.size).toBe(r.written.length);
  });

  it("a missing skills.csv is an error that names the path", () => {
    const empty = mkdtempSync(join(tmpdir(), "hk-copy-empty-"));
    expect(() => copyKit(empty, ws, { version: "1" })).toThrow(/skills\.csv/);
  });
});

describe("copyKit re-run", () => {
  it("a second run with the same kit changes nothing and reports all unchanged", () => {
    const first = copyKit(kit, ws, { version: "1.0.0" });
    const r = copyKit(kit, ws, { version: "1.0.0" });
    expect(r.written).toEqual([]);
    expect(r.upgraded).toEqual([]);
    expect(r.drifted).toEqual([]);
    expect(r.unchanged.length).toBe(first.written.length);
  });

  it("overwrites an untouched file whose kit source changed (upgraded) and records the new hash and version", () => {
    copyKit(kit, ws, { version: "1.0.0" });
    put(kit, "skills/foo/hk-foo/SKILL.md", "---\nname: hk-foo\n---\n\n# Foo v2\n");
    const r = copyKit(kit, ws, { version: "1.1.0" });
    expect(r.upgraded).toEqual(["docs/skills/foo/hk-foo/SKILL.md"]);
    expect(read(ws, "docs/skills/foo/hk-foo/SKILL.md")).toContain("v2");
    const m = readManifest(ws)!;
    expect(m.version).toBe("1.1.0");
    expect(m.hashes.get("docs/skills/foo/hk-foo/SKILL.md")).toBe(sha256(read(ws, "docs/skills/foo/hk-foo/SKILL.md")));
  });

  it("leaves a locally edited file alone and reports it drifted, even when the kit also moved", () => {
    copyKit(kit, ws, { version: "1.0.0" });
    put(ws, "docs/playbook/tenets.md", "# Tenets\n\nEdited locally.\n");
    put(kit, "docs/playbook/tenets.md", "# Tenets\n\nKit v2.\n");
    const r = copyKit(kit, ws, { version: "1.1.0" });
    expect(r.drifted).toEqual(["docs/playbook/tenets.md"]);
    expect(r.upgraded).toEqual([]);
    expect(read(ws, "docs/playbook/tenets.md")).toContain("Edited locally.");
  });

  it("keeps reporting a drifted file on later runs (manifest hash is not advanced)", () => {
    copyKit(kit, ws, { version: "1.0.0" });
    put(ws, "docs/playbook/tenets.md", "mine\n");
    copyKit(kit, ws, { version: "1.1.0" });
    expect(copyKit(kit, ws, { version: "1.1.0" }).drifted).toEqual(["docs/playbook/tenets.md"]);
  });

  it("never overwrites a file that exists on disk but is absent from the manifest, and reports it drifted", () => {
    put(ws, "docs/playbook/tenets.md", "# Mine, do not touch\n");
    const r = copyKit(kit, ws, { version: "1.0.0" });
    expect(read(ws, "docs/playbook/tenets.md")).toBe("# Mine, do not touch\n");
    expect(r.drifted).toContain("docs/playbook/tenets.md");
    expect(r.written).not.toContain("docs/playbook/tenets.md");
    expect(readManifest(ws)!.hashes.has("docs/playbook/tenets.md")).toBe(false);
  });

  it("rewrites a manifest-tracked file that was deleted from the workspace", () => {
    copyKit(kit, ws, { version: "1.0.0" });
    rmSync(join(ws, "docs/_INDEX.md"));
    const r = copyKit(kit, ws, { version: "1.0.0" });
    expect(r.written).toContain("docs/_INDEX.md");
    expect(read(ws, "docs/_INDEX.md")).toBe("# map\n");
  });

  it("never touches files the kit does not own", () => {
    copyKit(kit, ws, { version: "1.0.0" });
    put(ws, "docs/patterns/mine.md", "mine\n");
    copyKit(kit, ws, { version: "1.1.0" });
    expect(read(ws, "docs/patterns/mine.md")).toBe("mine\n");
  });
});

describe("check", () => {
  it("reports drift and upgrades-to-come without writing anything", () => {
    copyKit(kit, ws, { version: "1.0.0" });
    put(ws, "docs/playbook/tenets.md", "# Edited locally\n");
    put(kit, "skills/foo/hk-foo/SKILL.md", "---\nname: hk-foo\n---\n\n# Foo v2\n");
    const manifestBefore = read(ws, "docs/harnesskit/manifest");
    const r = check(kit, ws);
    expect(r.drifted).toEqual(["docs/playbook/tenets.md"]);
    expect(r.upgraded).toEqual(["docs/skills/foo/hk-foo/SKILL.md"]);
    expect(read(ws, "docs/playbook/tenets.md")).toBe("# Edited locally\n");
    expect(read(ws, "docs/skills/foo/hk-foo/SKILL.md")).toContain("# Foo\n");
    expect(read(ws, "docs/harnesskit/manifest")).toBe(manifestBefore);
  });

  it("on a fresh workspace reports every kit file as to-be-written and creates nothing", () => {
    const r = check(kit, ws);
    expect(r.written).toContain("docs/_INDEX.md");
    expect(existsSync(join(ws, "docs"))).toBe(false);
  });

  it("right after a copy reports nothing moved and nothing drifted", () => {
    copyKit(kit, ws, { version: "1.0.0" });
    const r = check(kit, ws);
    expect([r.written, r.upgraded, r.drifted]).toEqual([[], [], []]);
  });
});

// Python cases not ported: YOURS_FOLDERS seeding, docs/org, .claude/skills copying, git-commit manifest column,
// paths_to_hide and the --workspace guards (proof tooling / CLI concerns, not the copy module).

describe("copyKit adoption, indexes and seeds", () => {
  it("adopts a file that is on disk, not in the manifest, and byte-identical to the kit's", () => {
    put(ws, "docs/playbook/tenets.md", "# Tenets\n\nBe good.\n");
    const r = copyKit(kit, ws, { version: "1.0.0" });
    expect(r.drifted).toEqual([]);
    expect(r.unchanged).toContain("docs/playbook/tenets.md");
    expect(readManifest(ws)!.hashes.get("docs/playbook/tenets.md")).toBe(sha256("# Tenets\n\nBe good.\n"));
  });

  it("still reports a differing unrecorded file as drifted", () => {
    put(ws, "docs/playbook/tenets.md", "mine\n");
    expect(copyKit(kit, ws, { version: "1.0.0" }).drifted).toEqual(["docs/playbook/tenets.md"]);
  });

  it("copies the skills README, category READMEs of kit skills and a skills.csv holding only kit rows", () => {
    put(kit, "skills/README.md", "# Skills\n");
    put(kit, "skills/foo/README.md", "# Foo\n");
    put(kit, "skills/bar/README.md", "# Bar\n");
    copyKit(kit, ws, { version: "1.0.0" });
    expect(read(ws, "docs/skills/README.md")).toBe("# Skills\n");
    expect(read(ws, "docs/skills/foo/README.md")).toBe("# Foo\n");
    expect(existsSync(join(ws, "docs/skills/bar/README.md"))).toBe(false);
    expect(read(ws, "docs/skills/skills.csv")).toBe("name,category,tier,reason\nhk-foo,foo,kit,\n");
    expect(readManifest(ws)!.hashes.has("docs/skills/skills.csv")).toBe(true);
  });

  it("seeds the yours folders when absent, never records them, never overwrites them", () => {
    put(ws, "docs/glossary.md", "my words\n");
    const r = copyKit(kit, ws, { version: "1.0.0" });
    expect(read(ws, "docs/glossary.md")).toBe("my words\n");
    expect(read(ws, "docs/patterns/_INDEX.md")).toContain("Reusable learnings");
    for (const p of ["docs/testing_process/_INDEX.md", "docs/breadcrumb_analysis/_INDEX.md", "docs/guides/_INDEX.md"]) {
      expect(existsSync(join(ws, p))).toBe(true);
    }
    const hashes = readManifest(ws)!.hashes;
    expect(hashes.has("docs/glossary.md")).toBe(false);
    expect(hashes.has("docs/patterns/_INDEX.md")).toBe(false);
    expect(r.seeded).toContain("docs/patterns/_INDEX.md");
    expect(r.seeded).not.toContain("docs/glossary.md");
    expect(copyKit(kit, ws, { version: "1.0.0" }).seeded).toEqual([]);
  });

  it("records the workflow copies (skill stubs, guard) in the manifest, so an edited one drifts; nothing lands under .claude", () => {
    copyKit(kit, ws, { version: "1.0.0" });
    const stub = "docs/harnesskit/workflow/.claude/skills/hk-foo/SKILL.md";
    expect(read(ws, stub)).toContain("docs/skills/foo/hk-foo/SKILL.md");
    expect(existsSync(join(ws, "docs/harnesskit/workflow/.claude/skills/hk-bar"))).toBe(false);
    expect(existsSync(join(ws, ".claude"))).toBe(false);
    expect(readManifest(ws)!.hashes.has(stub)).toBe(true);
    writeFileSync(join(ws, stub), "mine\n");
    expect(copyKit(kit, ws, { version: "1.0.0" }).drifted).toEqual([stub]);
  });
});
