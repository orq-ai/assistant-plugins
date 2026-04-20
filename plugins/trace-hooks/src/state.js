import fs from "node:fs/promises";
import path from "node:path";

const STATE_ROOT =
  process.env.ORQ_CLAUDE_STATE_DIR ||
  path.join(process.env.HOME || process.env.USERPROFILE || "", ".claude", "state");

const BASE_STATE_DIR = path.join(STATE_ROOT, "orq_sessions");
const BASE_QUEUE_DIR = path.join(STATE_ROOT, "orq_queue");

function sanitizeSessionId(sessionId) {
  return path.basename(sessionId).replace(/[^a-zA-Z0-9_-]/g, "_");
}

function sessionFile(sessionId) {
  return path.join(BASE_STATE_DIR, `${sanitizeSessionId(sessionId)}.json`);
}

let _dirsReady;
export function ensureDirs() {
  if (!_dirsReady) {
    _dirsReady = Promise.all([
      fs.mkdir(BASE_STATE_DIR, { recursive: true }),
      fs.mkdir(BASE_QUEUE_DIR, { recursive: true }),
    ]).catch((err) => {
      _dirsReady = undefined;
      throw err;
    });
  }
  return _dirsReady;
}

const LOCK_STALE_MS = 10000;
const LOCK_RETRY_MS = 20;
const LOCK_MAX_ATTEMPTS = 150; // 150 * 20ms = 3s max wait

/**
 * Wraps a load-modify-save cycle with a file-based advisory lock.
 * Uses O_EXCL file creation which is atomic across processes.
 * Stale locks (from crashed hooks) are auto-broken after 10s.
 */
export async function withSessionLock(sessionId, fn) {
  await ensureDirs();
  const lockPath = `${sessionFile(sessionId)}.lock`;
  let acquired = false;

  for (let attempt = 0; attempt < LOCK_MAX_ATTEMPTS; attempt++) {
    try {
      const fh = await fs.open(lockPath, "wx");
      await fh.close();
      acquired = true;
      break;
    } catch (err) {
      if (err.code !== "EEXIST") {
        process.stderr.write(
          `[orq-trace] WARN: unexpected lock error (${err.code}): ${err.message}\n`,
        );
        break;
      }
      // Check for stale lock (hook crashed while holding it)
      try {
        const stat = await fs.stat(lockPath);
        if (Date.now() - stat.mtimeMs > LOCK_STALE_MS) {
          try { await fs.unlink(lockPath); } catch {}
          continue;
        }
      } catch {
        // Lock was released between our open and stat — retry
        continue;
      }
      await new Promise((r) => setTimeout(r, LOCK_RETRY_MS));
    }
  }

  if (!acquired) {
    // Lock not acquired after max attempts — log and proceed without lock.
    // Running unlocked is better than dropping the hook entirely, but may
    // produce a stale-read race in rare cases.
    process.stderr.write(
      `[orq-trace] WARN: failed to acquire session lock for ${sessionId}, proceeding unlocked\n`,
    );
  }

  try {
    return await fn();
  } finally {
    if (acquired) {
      try {
        await fs.unlink(lockPath);
      } catch (unlinkErr) {
        process.stderr.write(
          `[orq-trace] WARN: failed to release session lock: ${unlinkErr?.message}\n`,
        );
      }
    }
  }
}

export async function loadSessionState(sessionId) {
  try {
    await ensureDirs();
    const content = await fs.readFile(sessionFile(sessionId), "utf8");
    return JSON.parse(content);
  } catch (err) {
    if (err?.code === "ENOENT") return null;
    process.stderr.write(
      `[orq-trace] WARN: failed to load session state for ${sessionId}: ${err?.message}\n`,
    );
    if (err instanceof SyntaxError) {
      try { await fs.unlink(sessionFile(sessionId)); } catch {}
    }
    return null;
  }
}

export async function saveSessionState(sessionId, state) {
  await ensureDirs();
  // Atomic write: concurrent hook invocations otherwise race on the session
  // file and silently clobber each other's updates.
  const target = sessionFile(sessionId);
  const tmp = `${target}.${process.pid}.${Math.random().toString(36).slice(2)}.tmp`;
  await fs.writeFile(tmp, `${JSON.stringify(state, null, 2)}\n`, "utf8");
  await fs.rename(tmp, target);
}

export async function deleteSessionState(sessionId) {
  try {
    await ensureDirs();
    await fs.unlink(sessionFile(sessionId));
  } catch (err) {
    if (err?.code !== "ENOENT") {
      process.stderr.write(
        `[orq-trace] WARN: failed to delete session state: ${err?.message}\n`,
      );
    }
  }
}

const MAX_QUEUE_FILES = 100;

export async function enqueuePayload(payload) {
  await ensureDirs();

  // Cap queue size to prevent unbounded growth when endpoint is down
  const existing = await listQueuedFiles();
  if (existing.length >= MAX_QUEUE_FILES) {
    const toDrop = existing.slice(0, existing.length - MAX_QUEUE_FILES + 1);
    await Promise.all(toDrop.map((f) => deleteQueuedFile(f)));
  }

  const fileName = `${Date.now()}-${Math.random().toString(36).slice(2)}.json`;
  const filePath = path.join(BASE_QUEUE_DIR, fileName);
  await fs.writeFile(filePath, `${JSON.stringify(payload)}\n`, "utf8");
}

export async function listQueuedFiles() {
  try {
    await ensureDirs();
    const names = await fs.readdir(BASE_QUEUE_DIR);
    return names
      .filter((name) => name.endsWith(".json"))
      .sort()
      .map((name) => path.join(BASE_QUEUE_DIR, name));
  } catch (err) {
    if (err?.code !== "ENOENT") {
      process.stderr.write(`[orq-trace] WARN: failed to list queue files: ${err?.message}\n`);
    }
    return [];
  }
}

export async function readQueuedPayload(filePath) {
  const content = await fs.readFile(filePath, "utf8");
  return JSON.parse(content);
}

export async function deleteQueuedFile(filePath) {
  try {
    await fs.unlink(filePath);
  } catch {
    // Ignore delete failures
  }
}

const STALE_SESSION_MS = 24 * 60 * 60 * 1000; // 24 hours
const STALE_QUEUE_MS = 60 * 60 * 1000; // 1 hour

export async function pruneStaleFiles() {
  const now = Date.now();

  // Prune orphaned session files (mtime > 24h ago)
  try {
    const sessionNames = await fs.readdir(BASE_STATE_DIR);
    for (const name of sessionNames) {
      if (!name.endsWith(".json") && !name.endsWith(".lock")) continue;
      const filePath = path.join(BASE_STATE_DIR, name);
      try {
        const stat = await fs.stat(filePath);
        if (now - stat.mtimeMs > STALE_SESSION_MS) {
          await fs.unlink(filePath);
        }
      } catch {
        // Ignore individual file errors
      }
    }
  } catch {
    // Ignore if directory doesn't exist
  }

  // Prune stale queue files (mtime > 1h ago)
  try {
    const queueNames = await fs.readdir(BASE_QUEUE_DIR);
    for (const name of queueNames) {
      if (!name.endsWith(".json")) continue;
      const filePath = path.join(BASE_QUEUE_DIR, name);
      try {
        const stat = await fs.stat(filePath);
        if (now - stat.mtimeMs > STALE_QUEUE_MS) {
          await fs.unlink(filePath);
        }
      } catch {
        // Ignore individual file errors
      }
    }
  } catch {
    // Ignore if directory doesn't exist
  }
}
