import { useEffect, useState } from "react";
import { domainOf } from "../../lib/links";
import type { Preview } from "../../types";

// Previews are fetched once per URL per page load; a failed fetch is remembered as null.
const cache = new Map<string, Promise<Preview | null>>();

function fetchPreview(url: string): Promise<Preview | null> {
  let pending = cache.get(url);
  if (!pending) {
    pending = fetch(`/api/preview?url=${encodeURIComponent(url)}`)
      .then((r) => (r.ok ? (r.json() as Promise<Preview>) : null))
      .catch(() => null);
    cache.set(url, pending);
  }
  return pending;
}

function usePreview(url: string): Preview | null {
  const [preview, setPreview] = useState<Preview | null>(null);
  useEffect(() => {
    let live = true;
    void fetchPreview(url).then((p) => live && setPreview(p));
    return () => {
      live = false;
    };
  }, [url]);
  return preview;
}

/** A site's icon by domain. Images load across origins, so this works without the preview endpoint. */
function faviconFor(url: string): string {
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domainOf(url))}&sz=64`;
}

/**
 * The Messages rich link: the page's image on top and a footer with title and domain. Without
 * preview data (or before it arrives) it shows the site's icon, the title if known, and the domain.
 */
export function LinkPreview({ url }: { url: string }) {
  const preview = usePreview(url);
  const title = preview?.title ?? preview?.site_name ?? domainOf(url);
  const domain = domainOf(preview?.url ?? url);

  return (
    <a className="link-preview" href={url} target="_blank" rel="noreferrer">
      {preview?.image && <img className="link-preview-image" src={preview.image} alt="" loading="lazy" />}
      <span className="link-preview-footer">
        {!preview?.image && <img className="link-preview-icon" src={preview?.icon ?? faviconFor(url)} alt="" />}
        <span className="link-preview-text">
          <span className="link-preview-title">{title}</span>
          {preview?.image && preview.description && (
            <span className="link-preview-description">{preview.description}</span>
          )}
          <span className="link-preview-domain">{domain}</span>
        </span>
      </span>
    </a>
  );
}
