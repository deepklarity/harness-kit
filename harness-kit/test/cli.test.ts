import { describe, expect, it, vi } from "vitest";
import { main, USAGE } from "../src/cli.js";

async function run(argv: string[]) {
  const out: string[] = [];
  const err: string[] = [];
  vi.spyOn(process.stdout, "write").mockImplementation((s) => (out.push(String(s)), true));
  vi.spyOn(process.stderr, "write").mockImplementation((s) => (err.push(String(s)), true));
  const code = await main(argv);
  vi.restoreAllMocks();
  return { code, out: out.join(""), err: err.join("") };
}

describe("cli", () => {
  it("prints usage naming init and prove when asked for help", async () => {
    const r = await run(["--help"]);
    expect(r.code).toBe(0);
    expect(r.out).toBe(USAGE);
    expect(r.out).toContain("init");
    expect(r.out).toContain("prove");
  });

  it("prints usage and exits 1 with no command", async () => {
    const r = await run([]);
    expect(r.code).toBe(1);
    expect(r.out).toContain("init");
  });

  it("exits 1 on an unknown command and names it", async () => {
    const r = await run(["frobnicate"]);
    expect(r.code).toBe(1);
    expect(r.err).toContain("frobnicate");
    expect(r.err).toContain("prove");
  });
});
