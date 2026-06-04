#!/usr/bin/env node
import { handlePostToolUse, runSafely } from "../src/handlers.js";

await runSafely(handlePostToolUse);
