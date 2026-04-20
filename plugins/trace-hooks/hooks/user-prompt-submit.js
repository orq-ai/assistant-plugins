#!/usr/bin/env node
import { handleUserPromptSubmit, runSafely } from "../src/handlers.js";

await runSafely(handleUserPromptSubmit);
