import { main as proveMain } from "./prove.js";
import { resolve } from "node:path";
import { packageVersion, runInit } from "./init.js";

export const USAGE = `harnesskit <command>   (--version, --help)

Commands:
  init [--target DIR] [--check]   Set a git workspace up from the kit (--check writes nothing, exits 1 on drift)
  prove <scenario.md> [--runner herdr|headless] [--budget <usd>] [--dry-run]
                   Run one scenario with and without the kit and compare
  prove reply <slug>     Herdr runs: send the scenario's scripted reply to both panes when they wait on a question
  prove collect <slug>   Read both transcripts of a herdr-runner proof once the panes are done
  prove judge <slug>     Run the judge headless on a packet and render the page (--again to redo)
  prove analyze [<slug>] Where each run's time goes: analysis.json per scenario, analysis.md across them
  prove render           Rebuild writeup/proof/index.html from every run record
`;

const COMMANDS = new Set(["init", "prove"]);

/** Runs the CLI for argv (without node and script) and returns the exit code. */
export async function main(argv: string[]): Promise<number> {
  const [command] = argv;
  if (command === undefined || command === "--help" || command === "-h") {
    process.stdout.write(USAGE);
    return command === undefined ? 1 : 0;
  }
  if (command === "--version" || command === "-v") {
    process.stdout.write(`${packageVersion()}\n`);
    return 0;
  }
  if (!COMMANDS.has(command)) {
    process.stderr.write(`harnesskit: unknown command "${command}"\n\n${USAGE}`);
    return 1;
  }
  if (command === "init") {
    const rest = argv.slice(1);
    let target = process.cwd();
    let checkOnly = false;
    for (let i = 0; i < rest.length; i++) {
      if (rest[i] === "--check") checkOnly = true;
      else if (rest[i] === "--target" && rest[i + 1]) target = resolve(rest[++i]);
      else {
        process.stderr.write(`harnesskit init: unknown argument "${rest[i]}"\n\n${USAGE}`);
        return 1;
      }
    }
    try {
      const r = runInit({ target, check: checkOnly });
      process.stdout.write(r.output);
      return r.code;
    } catch (e) {
      process.stderr.write(`harnesskit init: ${(e as Error).message}\n`);
      return 1;
    }
  }
  return proveMain(argv.slice(1));
}
