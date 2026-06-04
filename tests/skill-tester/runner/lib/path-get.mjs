// path-get.mjs — read a value out of a parsed tool response by dot/bracket path.
//
// Supports keys, nested keys, and array indices written either way:
//   "id"            → obj.id
//   "result.id"     → obj.result.id
//   "data[0].key"   → obj.data[0].key
//   "[0].name"      → obj[0].name
// Returns undefined if any segment is missing (never throws).

export function pathGet(obj, path) {
  if (path == null || path === "") return obj;
  const segments = String(path)
    .replace(/\[(\d+)\]/g, ".$1") // data[0] → data.0
    .split(".")
    .filter((s) => s !== "");
  let cur = obj;
  for (const seg of segments) {
    if (cur == null) return undefined;
    cur = cur[seg];
  }
  return cur;
}
