#!/usr/bin/env node
import { handleStop, runSafely } from "../src/handlers.js";

await runSafely(handleStop);
