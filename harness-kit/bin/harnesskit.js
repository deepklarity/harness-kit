#!/usr/bin/env node
// Thin launcher: the CLI is compiled to dist/ at publish time, so a user's run needs only node.
import { main } from "../dist/cli.js";

process.exitCode = await main(process.argv.slice(2));
