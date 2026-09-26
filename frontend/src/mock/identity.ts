// Stand-in for POST /api/session: the backend will say whether a number is new. Until then,
// numbers this browser has entered before count as existing.
const KNOWN = "persona:known-numbers";
const LAST = "persona:last-number";

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Private mode or blocked storage: every number is new, which is fine for a demo.
  }
}

export function lastNumber(): string {
  return read(LAST) ?? "";
}

/** Records the number and reports whether it was new. */
export function enter(digits: string): { isNew: boolean } {
  const known: string[] = JSON.parse(read(KNOWN) ?? "[]");
  const isNew = !known.includes(digits);
  if (isNew) write(KNOWN, JSON.stringify([...known, digits]));
  write(LAST, digits);
  return { isNew };
}
