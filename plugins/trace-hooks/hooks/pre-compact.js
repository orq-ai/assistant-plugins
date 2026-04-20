#!/usr/bin/env node
import { handlePreCompact, runSafely } from "../src/handlers.js";

await runSafely(handlePreCompact);
