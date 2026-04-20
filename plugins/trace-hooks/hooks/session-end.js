#!/usr/bin/env node
import { handleSessionEnd, runSafely } from "../src/handlers.js";

await runSafely(handleSessionEnd);
