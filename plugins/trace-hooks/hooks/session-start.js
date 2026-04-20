#!/usr/bin/env node
import { handleSessionStart, runSafely } from "../src/handlers.js";

await runSafely(handleSessionStart);
