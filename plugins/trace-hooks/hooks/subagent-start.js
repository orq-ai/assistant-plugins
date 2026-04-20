#!/usr/bin/env node
import { handleSubagentStart, runSafely } from "../src/handlers.js";

await runSafely(handleSubagentStart);
