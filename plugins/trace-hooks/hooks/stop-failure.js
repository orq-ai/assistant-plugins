#!/usr/bin/env node
import { handleStopFailure, runSafely } from "../src/handlers.js";

await runSafely(handleStopFailure);
