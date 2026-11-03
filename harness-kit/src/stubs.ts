import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

// Where a workspace keeps skill bodies; the stub points here.
const WORKSPACE_SKILLS = "docs/skills";

const stubBody = (rel: string) => `
This skill lives in \`${rel}/\`. Read \`${rel}/SKILL.md\` and follow it exactly.

Every supporting file it mentions (REFERENCE.md, templates, \`scripts/\`, examples) sits beside it in that directory. Wherever it says "this skill's directory" or uses a path relative to itself, resolve it against \`${rel}/\`.
`;

/** Write outDir/<name>/SKILL.md for each skill of the given tiers in kitSkillsDir/skills.csv. */
export function generateStubs(
  kitSkillsDir: string,
  outDir: string,
  { tiers = ["kit"] }: { tiers?: string[] } = {},
): void {
  const rows = readFileSync(join(kitSkillsDir, "skills.csv"), "utf8")
    .split("\n")
    .slice(1)
    .filter((l) => l.trim())
    .map((l) => l.split(","));
  for (const [name, category, tier] of rows) {
    if (!tiers.includes(tier)) continue;
    const body = join(kitSkillsDir, category, name, "SKILL.md");
    let text: string;
    try {
      text = readFileSync(body, "utf8");
    } catch {
      throw new Error(`skills.csv lists "${name}" but ${category}/${name}/SKILL.md is missing (looked for ${body})`);
    }
    const m = /^---\n[\s\S]*?\n---\n/.exec(text);
    if (!m) throw new Error(`no frontmatter in ${body}`);
    const dir = join(outDir, name);
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, "SKILL.md"), m[0] + stubBody(`${WORKSPACE_SKILLS}/${category}/${name}`));
  }
}
