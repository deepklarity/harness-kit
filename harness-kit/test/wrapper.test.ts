import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { main } from "../src/cli.js";
import { detectInstallSpec } from "../src/init.js";
import { readManifest } from "../src/manifest.js";

const repo = () => {
  const d = mkdtempSync(join(tmpdir(), "hk-wrap-"));
  execFileSync("git", ["init", "-q", d]);
  return d;
};
const init = async (d: string) => {
  const w = process.stdout.write;
  process.stdout.write = (() => true) as typeof process.stdout.write;
  try {
    return await main(["init", "--target", d]);
  } finally {
    process.stdout.write = w;
  }
};
const wrapper = (d: string) => readFileSync(join(d, "docs/harnesskit/harnesskit"), "utf8");

describe("docs/harnesskit/harnesskit wrapper", () => {
  const saved = process.env.npm_config_package;
  afterEach(() => {
    if (saved === undefined) delete process.env.npm_config_package;
    else process.env.npm_config_package = saved;
  });

  it("runs the explicit npx package spec and records it", async () => {
    process.env.npm_config_package = "/tmp/it's/kit-0.1.0.tgz";
    const d = repo();
    await init(d);
    expect(wrapper(d)).toContain(`exec npx --yes --package='/tmp/it'\\''s/kit-0.1.0.tgz' harnesskit "$@"`);
    expect(wrapper(d).startsWith("#!/bin/sh\n")).toBe(true);
    expect(readManifest(d)!.installSpec).toBe("/tmp/it's/kit-0.1.0.tgz");
    expect(statSync(join(d, "docs/harnesskit/harnesskit")).mode & 0o111).toBeGreaterThan(0);
  });

  it("falls back to the registry form, and a re-run rewrites the wrapper", async () => {
    const d = repo();
    process.env.npm_config_package = "git+https://example.com/kit.git";
    await init(d);
    expect(wrapper(d)).toContain("git+https://example.com/kit.git");
    delete process.env.npm_config_package;
    await init(d);
    const version = JSON.parse(readFileSync("package.json", "utf8")).version;
    expect(wrapper(d)).toContain(`exec npx --yes @deepklarity/harnesskit@${version} "$@"`);
    expect(wrapper(d)).not.toContain("example.com");
    expect(readManifest(d)!.installSpec).toBe("@deepklarity/harnesskit");
    expect(existsSync(join(d, "docs/harnesskit/manifest"))).toBe(true);
  });

  it("runs a project install from the repo's own node_modules, with no machine path", async () => {
    const { wrapperScript } = await import("../src/init.js");
    const w = wrapperScript("project", "0.1.0");
    expect(w).toContain('exec "$(dirname "$0")/../../node_modules/.bin/harnesskit" "$@"');
    expect(w).not.toMatch(/\/(Users|home|private|tmp)\//);
  });

  it("does not track the wrapper as a drift source", async () => {
    const d = repo();
    await init(d);
    expect(readManifest(d)!.hashes.has("docs/harnesskit/harnesskit")).toBe(false);
  });

  it("detects a project install by path, but not the npx cache", () => {
    expect(detectInstallSpec({}, "/proj/node_modules/@deepklarity/harnesskit")).toBe("project");
    expect(detectInstallSpec({}, "/home/u/.npm/_npx/ab12/node_modules/@deepklarity/harnesskit")).toBe("@deepklarity/harnesskit");
    expect(detectInstallSpec({}, "/src/harness-kit")).toBe("@deepklarity/harnesskit");
  });
});

describe("harnesskit --version", () => {
  it("prints the package version and exits 0", async () => {
    let out = "";
    const w = process.stdout.write;
    process.stdout.write = ((s: string) => ((out += s), true)) as typeof process.stdout.write;
    try {
      expect(await main(["--version"])).toBe(0);
    } finally {
      process.stdout.write = w;
    }
    expect(out).toBe(`${JSON.parse(readFileSync("package.json", "utf8")).version}\n`);
  });
});
