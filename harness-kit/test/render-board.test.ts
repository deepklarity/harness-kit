import { execFileSync, spawnSync } from "node:child_process";
import { copyFileSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const SCRIPT = resolve("kit/templates/mission-control/render-board.mjs");
const FIXTURE = resolve("test/fixtures/board");
const CASES = resolve("test/fixtures/board-cases");
const START = "<!-- BOARD:START -->";
const END = "<!-- BOARD:END -->";
const STAMP = "2026-01-02T03:04:05";

/** Fixture page with the generated section emptied: the state the renderer starts from. */
function blankPage(): string {
  const page = readFileSync(join(FIXTURE, "mission-control.html"), "utf8");
  return page.slice(0, page.indexOf(START)) + START + page.slice(page.indexOf(END));
}

/** Render a board.csv inside a fresh initiative folder and return the resulting page. */
function render(csvPath: string, page = blankPage()): string {
  const dir = mkdtempSync(join(tmpdir(), "render-board-"));
  copyFileSync(csvPath, join(dir, "board.csv"));
  writeFileSync(join(dir, "mission-control.html"), page);
  execFileSync("node", [SCRIPT], { cwd: dir, env: { ...process.env, HK_RENDER_TIME: STAMP } });
  return readFileSync(join(dir, "mission-control.html"), "utf8");
}

describe("render-board.mjs", () => {
  it("warns on stderr when an unquoted comma gives a row too many fields", () => {
    const dir = mkdtempSync(join(tmpdir(), "render-board-"));
    writeFileSync(join(dir, "board.csv"), "id,kind,title,status,priority,depends_on,closes_when,notes\nB1,bug,Reads (sed, cat),open,P1,,x,y\n");
    writeFileSync(join(dir, "mission-control.html"), blankPage());
    const r = spawnSync("node", [SCRIPT], { cwd: dir, encoding: "utf8", env: { ...process.env, HK_RENDER_TIME: STAMP } });
    expect(r.stderr).toContain("board.csv: row B1 has 9 fields, expected 8; quote any field that contains a comma");
  });

  it("reproduces the golden page byte for byte", () => {
    const golden = readFileSync(resolve("test/fixtures/golden/board.html"), "utf8");
    expect(render(join(FIXTURE, "board.csv"))).toBe(golden);
  });

  it("is idempotent on an already rendered page", () => {
    const rendered = readFileSync(join(FIXTURE, "mission-control.html"), "utf8");
    expect(render(join(FIXTURE, "board.csv"), rendered)).toBe(rendered);
  });

  for (const name of readdirSync(CASES)) {
    it(`matches the expected output for case ${name}`, () => {
      const expected = readFileSync(join(CASES, name, "expected.html"), "utf8");
      expect(render(join(CASES, name, "board.csv"))).toBe(expected);
    });
  }

  describe("the renderer owns the page shell", () => {
    const STALE = "<style>body{background:#fff}</style>";
    const staleHead = (page: string) => page.replace(/<style>[^]*<\/style>/, STALE).replace("<title>Mission Control</title>", "<title>My board</title>");

    it("rewrites the head from the installed template, keeping the page's own title and the prose outside the markers", () => {
      const page = staleHead(blankPage()).replace("<main>", "<main><!-- agent note -->");
      expect(page).toContain(STALE);
      const out = render(join(FIXTURE, "board.csv"), page);
      expect(out).not.toContain(STALE);
      expect(out).toContain(readFileSync(resolve("kit/templates/mission-control/mission-control.html"), "utf8").match(/<style>[^]*<\/style>/)![0]);
      expect(out).toContain("<title>My board</title>");
      expect(out).toContain("<!-- agent note -->");
      expect(out).toContain("<h1>");
    });

    it("takes the initiative folder as an argument and leaves the working directory alone", () => {
      const dir = mkdtempSync(join(tmpdir(), "render-board-arg-"));
      copyFileSync(join(FIXTURE, "board.csv"), join(dir, "board.csv"));
      writeFileSync(join(dir, "mission-control.html"), blankPage());
      execFileSync("node", [SCRIPT, dir], { cwd: tmpdir(), env: { ...process.env, HK_RENDER_TIME: STAMP } });
      expect(readFileSync(join(dir, "mission-control.html"), "utf8")).toBe(readFileSync(resolve("test/fixtures/golden/board.html"), "utf8"));
    });

    it("tells the reader to refresh with the installed renderer, not a copy in the folder", () => {
      const out = render(join(FIXTURE, "board.csv"));
      expect(out).toContain("node docs/harnesskit/templates/mission-control/render-board.mjs");
      expect(out).not.toContain("run node render-board.mjs in this folder");
    });
  });

  it("fails when the markers are missing", () => {
    const dir = mkdtempSync(join(tmpdir(), "render-board-"));
    copyFileSync(join(FIXTURE, "board.csv"), join(dir, "board.csv"));
    writeFileSync(join(dir, "mission-control.html"), "<html></html>");
    const r = spawnSync("node", [SCRIPT], { cwd: dir, encoding: "utf8" });
    expect(r.status).toBe(1);
    expect(r.stderr).toContain("markers missing in mission-control.html");
  });

  describe("board rules", () => {
    const HEAD = "id,kind,title,status,priority,depends_on,closes_when,notes\n";
    function renderCsv(body: string, log?: string): string {
      const dir = mkdtempSync(join(tmpdir(), "render-board-csv-"));
      writeFileSync(join(dir, "board.csv"), HEAD + body);
      writeFileSync(join(dir, "mission-control.html"), blankPage());
      if (log !== undefined) writeFileSync(join(dir, "log.md"), log);
      execFileSync("node", [SCRIPT, dir], { env: { ...process.env, HK_RENDER_TIME: STAMP } });
      return readFileSync(join(dir, "mission-control.html"), "utf8");
    }
    const section = (html: string, id: string) => {
      const s = html.slice(html.indexOf(`<section id="${id}"`));
      return s.slice(0, s.indexOf("</section>"));
    };
    /** Ids of the work rows, in page order. */
    const workOrder = (html: string) => [...section(html, "work").matchAll(/<tbody class="item" id="([^"]+)"/g)].map((m) => m[1]);
    const BOARD =
      "D02,decision,What does the root .gitignore ignore?,open,P0,,Owner answers,Proposed: add writeup/ and .DS_Store. Mission control is scratch.\n" +
      "D03,decision,What do we call the stack script?,open,P1,,Owner answers,Proposed: stack.sh. dev.sh is the only one.\n" +
      "D01,decision,Pick a name,decided,P1,,yes,n\n" +
      "T1,task,Write the logs guide,open,P0,,A doc under docs/guides/logs.md names every path,See ops/README.md for paths.\n" +
      "T2,task,Wire the gate that runs every suite at the root of the workspace,open,P1,D02;T1,x,n\n" +
      "T3,task,Old thing,done,P1,,x,n\n";

    it("has no modal: no dialog element and no showModal anywhere", () => {
      const html = renderCsv(BOARD);
      expect(html).not.toContain("<dialog");
      expect(html).not.toContain("showModal");
    });

    it("puts what needs you first, before the work", () => {
      const inner = renderCsv(BOARD).slice(renderCsv(BOARD).indexOf(START));
      expect(inner.indexOf('<section id="owner">')).toBeLessThan(inner.indexOf('<section id="work">'));
    });

    it("gives each open decision its proposed answer and one copy button, and says the reply format in the row", () => {
      const owner = section(renderCsv(BOARD), "owner");
      expect(owner).toContain("What does the root .gitignore ignore?");
      expect(owner).toContain(">writeup/</a> and .DS_Store.");
      expect(owner.match(/data-copy="D02 yes"/g)?.length).toBe(1);
      expect(owner).toContain("<strong>To close it:</strong> Reply &quot;D02 yes&quot; to take the proposed answer, or &quot;D02: your answer&quot;.");
      expect(owner).not.toContain("D01 yes");
    });

    it("keeps why in the opened row and names the rows a decision holds up by whole title", () => {
      const owner = section(renderCsv(BOARD), "owner");
      expect(owner).toContain("Mission control is scratch.");
      expect(owner).toMatch(/<p class="dl">Holds up<\/p><ul><li><a href="#T2">T2 Wire the gate that runs every suite at the root of the workspace<\/a>/);
      expect(owner).not.toContain("…");
    });

    it("a blocked row names what it waits on in the status column and by whole title in its detail", () => {
      const work = section(renderCsv(BOARD), "work");
      const row = work.slice(work.indexOf('<tbody class="item" id="T2"'));
      const one = row.slice(0, row.indexOf("</tbody>"));
      expect(one).toContain('<span class="pill blocked">blocked</span>');
      expect(one).toContain('after <a href="#D02">D02</a>, <a href="#T1">T1</a>');
      expect(one).toContain("D02 What does the root .gitignore ignore?");
      expect(one).toContain("T1 Write the logs guide");
    });

    it("opens a row's detail in place and links workspace paths", () => {
      const work = section(renderCsv(BOARD), "work");
      const row = work.slice(work.indexOf('<tbody class="item" id="T1"'));
      const one = row.slice(0, row.indexOf("</tbody>"));
      expect(one).toContain('<a href="../../docs/guides/logs.md">docs/guides/logs.md</a>');
      expect(one).toContain('<a href="../../ops/README.md">ops/README.md</a>');
    });

    it("a row is ready once every dependency is closed", () => {
      const html = renderCsv(BOARD.replace("D02,decision,What does the root .gitignore ignore?,open", "D02,decision,What does the root .gitignore ignore?,decided").replace("T1,task,Write the logs guide,open", "T1,task,Write the logs guide,done"));
      expect(section(html, "work")).toContain('<span class="pill open">open</span>');
      expect(section(html, "work")).not.toContain("pill blocked");
    });

    it("orders open rows by priority then dependency order", () => {
      const html = renderCsv(
        "A,task,P2 ready,open,P2,,x,n\n" +
          "B,task,P0 blocked on C,open,P0,C,x,n\n" +
          "C,task,P0 blocked on E,open,P0,E,x,n\n" +
          "D,task,P1 ready,open,P1,,x,n\n" +
          "E,task,root,open,P2,,x,n\n" +
          "F,task,P0 ready,open,P0,,x,n\n",
      );
      expect(workOrder(html)).toEqual(["F", "C", "B", "D", "A", "E"]);
    });

    it("adds a text filter past fifteen open rows and none below", () => {
      const many = Array.from({ length: 16 }, (_, i) => `T${i},task,Row ${i},open,P1,,x,n\n`).join("");
      expect(section(renderCsv(many), "work")).toMatch(/<input[^>]*type="search"[^>]*id="filter"/);
      expect(section(renderCsv(BOARD), "work")).not.toContain('id="filter"');
    });

    it("stamps the render time, tool and source under the verdict, with the newest log line", () => {
      const html = renderCsv(BOARD, "# Log\n\nintro\n\n- first entry\n- Guard hook: filled PROTECTED.\n");
      expect(html).toContain(`Rendered <time datetime="${STAMP}">2026-01-02 03:04</time> by render-board.mjs from board.csv · Last: Guard hook: filled PROTECTED.`);
      const inner = html.slice(html.indexOf(START), html.indexOf(END));
      expect(inner).toMatch(/<!-- [^]*node docs\/harnesskit\/templates\/mission-control\/render-board\.mjs[^]*-->/);
      expect(renderCsv(BOARD)).not.toContain("Last:");
    });

    it("adds the Rendered line to a page that has none, and replaces it on later renders", () => {
      const dir = mkdtempSync(join(tmpdir(), "render-board-stamp-"));
      writeFileSync(join(dir, "board.csv"), HEAD + BOARD);
      writeFileSync(join(dir, "mission-control.html"), blankPage().replace(/<p class="muted" id="updated">[^]*?<\/p>\n?/, ""));
      const run = (t: string) => execFileSync("node", [SCRIPT, dir], { env: { ...process.env, HK_RENDER_TIME: t } });
      run(STAMP);
      run("2026-02-03T04:05:06");
      const out = readFileSync(join(dir, "mission-control.html"), "utf8");
      expect(out.match(/id="updated"/g)).toHaveLength(1);
      expect(out).toContain("2026-02-03 04:05");
    });

    it("stamps the local clock when no override is set", () => {
      const dir = mkdtempSync(join(tmpdir(), "render-board-"));
      writeFileSync(join(dir, "board.csv"), HEAD + BOARD);
      writeFileSync(join(dir, "mission-control.html"), blankPage());
      const env = { ...process.env };
      delete env.HK_RENDER_TIME;
      execFileSync("node", [SCRIPT], { cwd: dir, env });
      expect(readFileSync(join(dir, "mission-control.html"), "utf8")).toMatch(/Rendered <time datetime="\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}">\d{4}-\d{2}-\d{2} \d{2}:\d{2}<\/time> by render-board\.mjs/);
    });

    it("the template has no dialog styles, no dark palette and no network references", () => {
      const tpl = readFileSync(resolve("kit/templates/mission-control/mission-control.html"), "utf8");
      expect(tpl).not.toMatch(/dialog/);
      expect(tpl).not.toMatch(/prefers-color-scheme/);
      expect(tpl).not.toMatch(/https?:\/\//);
    });

    it("the rendered page passes the surface check except its deliberate always-light theme", () => {
      const dir = mkdtempSync(join(tmpdir(), "render-board-check-"));
      writeFileSync(join(dir, "page.html"), render(join(FIXTURE, "board.csv")));
      const r = spawnSync("node", [resolve("kit/skills/audits/hk-surface-design/scripts/check_surface.mjs"), join(dir, "page.html")], { encoding: "utf8" });
      const lines = r.stdout.split("\n").filter((l) => /^\d+-/.test(l) && !l.startsWith("13-filled") && !l.startsWith("12-theme"));
      expect(lines).toEqual([]);
    });

    /** The text of the first element of a tag and class, tags stripped. */
    const textOf = (html: string, tag: string, cls: string) => {
      const m = html.match(new RegExp(`<${tag}[^>]*class="${cls}"[^>]*>([^]*?)</${tag}>`));
      return m ? m[1].replace(/<[^>]*>/g, "") : null;
    };
    /** The opened part of each ask in the owner section. */
    const asks = (html: string) => section(html, "owner").split('<tbody class="item"').slice(1);

    describe("decision rows state the real proposal", () => {
      it("shows the sentence after Proposed: in What you do, not closes_when, and keeps the rest as notes", () => {
        const ask = asks(renderCsv(BOARD))[0];
        expect(ask).toContain("Take or change the proposed answer: Add");
        expect(ask).not.toContain("Owner answers");
        const notes = ask.slice(ask.indexOf('<p class="dl">Notes</p>'));
        expect(notes).toContain("Mission control is scratch.");
        expect(notes).not.toContain("Proposed:");
      });

      it("stops the proposal at the end of its sentence, not at a dot inside a path or name", () => {
        const html = renderCsv("D1,decision,Name?,open,P0,,owner answers,\"Proposed: @deepklarity/harnesskit v1.2 in kit/docs/a.md. It matches the scope.\"\n");
        expect(asks(html)[0]).toContain("@deepklarity/harnesskit v1.2 in <a href=\"../../kit/docs/a.md\">kit/docs/a.md</a>.</p>");
      });

      it("stops the proposal at a line break", () => {
        const html = renderCsv('D1,decision,Name?,open,P0,,x,"Proposed: take the default\nbecause nobody objects"\n');
        expect(asks(html)[0]).toContain("Take or change the proposed answer: Take the default</p>");
      });

      it("with no Proposed: in notes, falls back to closes_when and offers no yes button", () => {
        const html = renderCsv("D1,decision,Name?,open,P0,,owner answers,The kit builds the harness around the code.\n");
        const ask = asks(html)[0];
        expect(ask).not.toContain('data-copy="D1 yes"');
        expect(ask).toContain("<strong>Do:</strong> owner answers");
        expect(ask).toContain("The kit builds the harness around the code.");
      });

      it("shows the decided answer from notes on a decided row in The work", () => {
        const html = renderCsv("D1,decision,Name?,decided,P0,,x,Decided: node only. Python removed in v2.\n");
        const closed = section(html, "work").slice(section(html, "work").indexOf("1 closed row, with their proof"));
        expect(closed).toContain("Decided: Node only.");
        expect(closed).toContain("Python removed in v2.");
      });
    });

    describe("unfilled slots", () => {
      /** The shell with its tokens, as a fresh adopter has it. */
      const withTokens = () => readFileSync(resolve("kit/templates/mission-control/mission-control.html"), "utf8");
      const renderPage = (page: string) => {
        const dir = mkdtempSync(join(tmpdir(), "render-board-slots-"));
        writeFileSync(join(dir, "board.csv"), HEAD + BOARD);
        writeFileSync(join(dir, "mission-control.html"), page);
        execFileSync("node", [SCRIPT, dir], { env: { ...process.env, HK_RENDER_TIME: STAMP } });
        return readFileSync(join(dir, "mission-control.html"), "utf8");
      };
      const visible = (html: string) => html.replace(/<!--[^]*?-->/g, "");

      it("shows no raw braces to the reader and names each slot in words", () => {
        const out = renderPage(withTokens());
        expect(visible(out)).not.toMatch(/\{\{\s*[\w.-]+\s*\}\}/);
        const slots = [...out.matchAll(/<span class="unfilled" data-slot="([\w.-]+)">([^<]*)<\/span>/g)].map((m) => [m[1], m[2]]);
        expect(slots.slice(0, 6)).toEqual([
          ["initiative", "initiative not written yet"],
          ["verdict", "verdict not written yet"],
          ["done", "done not written yet"],
          ["waiting_on_you", "waiting on you not written yet"],
          ["next_for_me", "next for me not written yet"],
          ["broken", "broken not written yet"],
        ]);
        expect(out).toMatch(/<td><span class="unfilled" data-slot="link">link not written yet<\/span><\/td>/);
      });

      it("leaves a token inside an HTML comment alone", () => {
        expect(renderPage(withTokens())).toContain("<!-- Fill every {{token}} (an unfilled one");
      });

      it("keeps Where are we? and the references outside the generated markers, kept across renders", () => {
        const once = renderPage(withTokens());
        const filled = once.replace(/<span class="unfilled" data-slot="done">[^<]*<\/span>/, "Gate runs.");
        const out = renderPage(filled);
        expect(out).toContain("<p><strong>Done:</strong> Gate runs.</p>");
        expect(out.indexOf("Gate runs.")).toBeLessThan(out.indexOf(START));
      });

      it("is idempotent and a filled slot stays filled", () => {
        const once = renderPage(withTokens());
        expect(renderPage(once)).toBe(once);
        const filled = once.replace(/<span class="unfilled" data-slot="verdict">[^<]*<\/span>/, "Shipped; next is the guard hook");
        const out = renderPage(filled);
        expect(out).toContain('<p class="verdict">Shipped; next is the guard hook</p>');
        expect(out).toContain('data-slot="link"');
      });

      it("a token written back into a rendered page is still recognised", () => {
        const out = renderPage(renderPage(withTokens()).replace(/<span class="unfilled" data-slot="verdict">[^<]*<\/span>/, "{{verdict}}"));
        expect(out).toContain('<p class="verdict"><span class="unfilled" data-slot="verdict">verdict not written yet</span></p>');
      });

      it("styles the placeholder muted italic in the shell", () => {
        const css = withTokens().match(/\.unfilled\{[^}]*\}/)?.[0] ?? "";
        expect(css).toContain("italic");
        expect(css).toContain("#5b6570");
      });
    });

    describe("one fold for suggestions and learnings", () => {
      const NOTICED =
        "S1,suggestion,Dedupe the index,noted,P2,,,kit/docs/_INDEX.md\n" +
        "S2,suggestion,Drop the zip step,noted,P2,,,src/pack.py\n" +
        "S3,suggestion,Old idea,taken,P2,,,\n" +
        "L1,learning,Rename needs constants first,open,P1,,,\n" +
        "L2,learning,Goldens before ports,folded,P2,,,\n";
      const topFolds = (html: string) => (html.slice(html.indexOf(START), html.indexOf(END)).match(/<details class="fold"><summary>([^<]*)<\/summary>/g) ?? []).map((m) => m.replace(/<[^>]*>/g, "")).filter((t) => !t.startsWith("Already acted on") && !t.includes("closed row"));

      it("renders exactly one fold titled with open counts in words, after The work", () => {
        const html = renderCsv(NOTICED + BOARD);
        expect(html.match(/<section id="(noticed|learnings)"/g)).toEqual(['<section id="noticed"']);
        expect(topFolds(html)).toEqual(["Noticed along the way: two suggestions for the repo owners, one learning"]);
        expect(html.indexOf('<section id="work"')).toBeLessThan(html.indexOf('<section id="noticed"'));
      });

      it("lists the open items first and the closed ones in one nested Already acted on fold", () => {
        const s = section(renderCsv(NOTICED), "noticed");
        const nested = s.indexOf('<details class="fold"><summary>Already acted on (2)</summary>');
        expect(nested).toBeGreaterThan(0);
        for (const open of ["Dedupe the index", "Drop the zip step", "Rename needs constants first"]) {
          expect(s.indexOf(open)).toBeGreaterThan(0);
          expect(s.indexOf(open)).toBeLessThan(nested);
        }
        for (const closed of ["Old idea", "Goldens before ports"]) expect(s.indexOf(closed)).toBeGreaterThan(nested);
        expect(s.match(/Already acted on/g)?.length).toBe(1);
      });

      it("has no nested fold when nothing is closed, and says all acted on when nothing is open", () => {
        expect(section(renderCsv("S1,suggestion,A,noted,P2,,,x\n"), "noticed")).not.toContain("Already acted on");
        expect(topFolds(renderCsv("S1,suggestion,A,taken,P2,,,x\n"))).toEqual(["Noticed along the way: all acted on"]);
      });

      it("names a single kind in the title", () => {
        expect(topFolds(renderCsv("L1,learning,A,open,P1,,,\n"))).toEqual(["Noticed along the way: one learning"]);
      });

      it("never puts suggestions or learnings in What needs you or The work", () => {
        const html = renderCsv(NOTICED);
        expect(section(html, "owner")).not.toContain("Dedupe");
        expect(section(html, "work")).not.toContain("Rename needs");
      });
    });

    it("creates the page from the installed template when the folder has only board.csv", () => {
      const dir = mkdtempSync(join(tmpdir(), "render-board-new-"));
      writeFileSync(join(dir, "board.csv"), HEAD + BOARD);
      const r = spawnSync("node", [SCRIPT, dir], { encoding: "utf8", env: { ...process.env, HK_RENDER_TIME: STAMP } });
      expect(r.status).toBe(0);
      expect(r.stderr).toBe("");
      expect(r.stdout.trim().split("\n")).toHaveLength(1);
      expect(r.stdout).toContain("created mission-control.html");
      const out = readFileSync(join(dir, "mission-control.html"), "utf8");
      expect(out).toContain('<p class="label">What needs you</p>');
      expect(out).toContain("2 with the agent, 2 waiting on you (listed above, not here), 2 closed");
      expect(out).toContain("<title>Mission Control</title>");
    });

    it("does not say it created the page when it already existed", () => {
      const dir = mkdtempSync(join(tmpdir(), "render-board-old-"));
      writeFileSync(join(dir, "board.csv"), HEAD + BOARD);
      writeFileSync(join(dir, "mission-control.html"), blankPage());
      const r = spawnSync("node", [SCRIPT, dir], { encoding: "utf8" });
      expect(r.stdout).not.toContain("created");
    });
  });
});
