import { mkdtempSync, realpathSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { trustedByClaude } from "../src/prove.js";

describe("trustedByClaude", () => {
  const ws = mkdtempSync(join(tmpdir(), "trust-ws-"));
  const config = join(mkdtempSync(join(tmpdir(), "trust-cfg-")), ".claude.json");

  it("is true only for a folder Claude Code recorded as trusted, matched on its real path", () => {
    writeFileSync(config, JSON.stringify({ projects: { [realpathSync(ws)]: { hasTrustDialogAccepted: true } } }));
    expect(trustedByClaude(ws, config)).toBe(true);
    expect(trustedByClaude(tmpdir(), config)).toBe(false);
  });

  it("is false when the trust flag is unset or the config is missing or unreadable", () => {
    writeFileSync(config, JSON.stringify({ projects: { [realpathSync(ws)]: {} } }));
    expect(trustedByClaude(ws, config)).toBe(false);
    writeFileSync(config, "{not json");
    expect(trustedByClaude(ws, config)).toBe(false);
    expect(trustedByClaude(ws, join(tmpdir(), "absent", ".claude.json"))).toBe(false);
  });
});
