// Browser globals, read defensively so this module tree can be imported
// under plain node.
//
// Why this file exists: the overlay is the surface the hall reads and it had
// no tests, because `store.ts` and `settings.ts` both touched `location` and
// `localStorage` at MODULE level. Importing either one outside a browser threw
// before a single test could run, so the code that failed in front of a full
// congregation was the only code in the repo that could not be tested.
//
// These are deliberately not a jsdom shim. A shim would let a test pass while
// depending on a browser detail that vMix's Chromium 51 does not have. This
// gives back exactly what the callers need — query params, an origin, a
// key/value store — and honest empty answers when there is no browser.

/** Query string of the current document, or "" when there is no browser. */
export function search(): string {
  return typeof location === "undefined" ? "" : location.search;
}

/** Parsed query params. Empty (not undefined) off-browser, so callers need no guard. */
export function params(): URLSearchParams {
  return new URLSearchParams(search());
}

/** Origin of the current document, or "" when there is no browser. */
export function origin(): string {
  return typeof location === "undefined" ? "" : location.origin;
}

// localStorage throws rather than returning null in a few real situations —
// Safari private browsing, and a file:// origin — so every access is guarded.
// A settings read that throws would take the overlay down with it, and the
// overlay going down is a blank caption bar in a full hall.

/** Stored value for `key`, or null when unavailable for any reason. */
export function readStored(key: string): string | null {
  try {
    if (typeof localStorage === "undefined") return null;
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** Store `value` under `key`. Silently does nothing when unavailable. */
export function writeStored(key: string, value: string): void {
  try {
    if (typeof localStorage === "undefined") return;
    localStorage.setItem(key, value);
  } catch {
    /* ignore — persistence is a convenience, never a requirement */
  }
}
