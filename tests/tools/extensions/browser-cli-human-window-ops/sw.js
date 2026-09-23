// Manual test-only service worker: keeps the extension installable and lets the
// ops page wake it if needed. It performs no window operations by itself.
chrome.runtime.onInstalled.addListener(() => {});
chrome.runtime.onStartup.addListener(() => {});
