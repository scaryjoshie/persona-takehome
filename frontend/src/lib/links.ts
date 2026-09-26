// Finding links in message text, the way Messages does: a scheme, a www., or a bare domain on a
// common top-level domain counts; trailing punctuation does not.
const TLDS = "com|org|net|io|dev|app|ai|co|edu|gov|me|ly|so|gg|us|uk|ca|de";
const URL_PATTERN = new RegExp(
  String.raw`(?<![@\w.-])(?:https?:\/\/[^\s<>"]+|www\.[^\s<>"]+|[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:${TLDS})\b(?:\/[^\s<>"]*)?)`,
  "gi",
);
const TRAILING = /[.,;:!?)\]'’"]+$/;

export interface TextPart {
  text: string;
  /** Set when this part is a link: the absolute URL to open. */
  href?: string;
}

/** Splits text into plain runs and links. */
export function splitLinks(text: string): TextPart[] {
  const parts: TextPart[] = [];
  let last = 0;
  for (const match of text.matchAll(URL_PATTERN)) {
    const start = match.index ?? 0;
    const link = match[0].replace(TRAILING, "");
    if (start > last) parts.push({ text: text.slice(last, start) });
    parts.push({ text: link, href: absolute(link) });
    last = start + link.length;
  }
  if (last < text.length) parts.push({ text: text.slice(last) });
  return parts;
}

/** The first link in the text, if any. */
export function firstLink(text: string): string | null {
  return splitLinks(text).find((p) => p.href)?.href ?? null;
}

/** True when the message is nothing but a link, which Messages shows as a card alone. */
export function isOnlyLink(text: string): boolean {
  const parts = splitLinks(text.trim());
  return parts.length === 1 && parts[0].href !== undefined;
}

export function domainOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function absolute(link: string): string {
  return /^https?:\/\//i.test(link) ? link : `https://${link}`;
}
