/**
 * Behind the HTTPS proxy (Caddy) the knowledge server lives under /knowledge on the same domain.
 * Locally without the proxy (LibreChat directly on :3080) it is reached on port 8100.
 */
export const KB_URL =
  window.location.port === '3080'
    ? `${window.location.protocol}//${window.location.hostname}:8100`
    : `${window.location.origin}/knowledge`;
export const KB_ORIGIN = new URL(KB_URL).origin;
