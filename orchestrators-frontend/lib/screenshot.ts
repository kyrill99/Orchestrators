/**
 * Screenshot of the current page, taken in the browser from the DOM.
 *
 * modern-screenshot clones the DOM into an SVG <foreignObject> and lets the
 * browser itself paint it onto a canvas. Because the browser does the
 * painting, modern CSS that canvas re-implementations (html2canvas) choke on,
 * such as oklch() colours, color-mix() and backdrop filters, renders as on
 * screen; next/font's self-hosted fonts are same-origin and get embedded.
 * Scroll positions inside panes are kept. We prefer it over
 * getDisplayMedia, which needs a permission prompt and a tab picker on every
 * report and is unavailable on most mobile browsers.
 */

const MAX_BYTES = 4.5 * 1024 * 1024; // backend limit is 5 MB

/** Elements with this attribute are left out of the screenshot. */
export const SCREENSHOT_IGNORE = "data-screenshot-ignore";

export async function capturePage(): Promise<Blob> {
  const { domToBlob } = await import("modern-screenshot");
  const root = document.body;
  const options = {
    width: window.innerWidth,
    height: window.innerHeight,
    scale: Math.min(window.devicePixelRatio || 1, 2),
    backgroundColor: getComputedStyle(root).backgroundColor || "#fff",
    timeout: 8000,
    filter: (node: Node) =>
      !(node instanceof Element && node.hasAttribute(SCREENSHOT_IGNORE)),
  };
  let blob = await domToBlob(root, { ...options, type: "image/png" });
  if (blob.size > MAX_BYTES) {
    blob = await domToBlob(root, { ...options, type: "image/jpeg", quality: 0.85 });
  }
  if (blob.size > MAX_BYTES) {
    blob = await domToBlob(root, { ...options, scale: 1, type: "image/jpeg", quality: 0.75 });
  }
  return blob;
}

export function screenshotFilename(blob: Blob): string {
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
  return `screenshot-${stamp}.${blob.type === "image/jpeg" ? "jpg" : "png"}`;
}

export function pageContext() {
  return {
    url: window.location.href,
    userAgent: navigator.userAgent,
    viewport: `${window.innerWidth}×${window.innerHeight}`,
    devicePixelRatio: window.devicePixelRatio || 1,
  };
}
