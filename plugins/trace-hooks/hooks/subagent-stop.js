#!/usr/bin/env node
import { handleSubagentStop, runSafely } from "../src/handlers.js";

await runSafely(handleSubagentStop);
