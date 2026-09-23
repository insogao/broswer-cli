// Test-only service worker for the Browser CLI window-focus fixture.
// Its only purpose is to make the unpacked extension start once on install and
// expose a discoverable chrome-extension://<id>/sw.js target so the CDP harness
// can learn the extension id for this throwaway profile.

chrome.runtime.onInstalled.addListener(() => {});
chrome.runtime.onStartup.addListener(() => {});
