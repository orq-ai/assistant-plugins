#!/usr/bin/env node
import { handlePostToolUseFailure, runSafely } from "../src/handlers.js";

await runSafely(handlePostToolUseFailure);
