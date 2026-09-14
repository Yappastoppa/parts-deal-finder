// Public configuration only. Use an HTTPS backend origin (no trailing /api) to enable live mode.
// Empty keeps the GitHub Pages demo working without any backend. Never put credentials here.
export const API_BASE_URL = 'https://parts-deal-finder-production.up.railway.app';

// Optional AI help server origin; independent of live inventory. No keys belong here.
export const HELP_API_BASE_URL = '';
