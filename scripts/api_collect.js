// API-Rip: Collect captured traffic
// Uses top-level await (gstack browse wraps in async IIFE automatically)

if (!window.__API_RIP__) {
  return JSON.stringify({error: 'Interceptor not installed. Run api_intercept.js first.'});
}

// Replay missed requests from Performance API (page is fully loaded by now)
var __replayed = 0;
if (window.__API_RIP__.replay) {
  __replayed = await window.__API_RIP__.replay();
}

// Wait for replay fetches to settle
if (__replayed > 0) {
  await new Promise(function(r) { setTimeout(r, 2000); });
}

return JSON.stringify({
  captures: window.__API_RIP__.getCaptures(),
  count: window.__API_RIP__.getCaptureCount(),
  replayed: __replayed,
  pageUrl: location.href,
  timestamp: new Date().toISOString(),
});
