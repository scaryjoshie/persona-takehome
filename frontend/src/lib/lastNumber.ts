// The number entered last, so returning is one click. Storage can be unavailable; that is fine.
const KEY = "persona:last-number";

export function lastNumber(): string {
  try {
    return localStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

export function rememberNumber(digits: string) {
  try {
    localStorage.setItem(KEY, digits);
  } catch {
    // ignore
  }
}
