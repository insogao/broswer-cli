// Test-only page script for the Browser CLI window-focus fixture.
//
// The CDP harness loads chrome-extension://<id>/test.html in a managed
// background window and evaluates:
//   __bcliRunWindowFocusTest(<url>)
// with awaitPromise. The function uses the real Extension API:
//   1. chrome.windows.create({url, focused: true})  -> createResult
//   2. chrome.windows.update(id, {focused: true})  -> updateResult
// Both API-level outcomes (resolution or rejection) and the returned
// window/tab identifiers are reported verbatim; whether macOS native focus was
// actually granted is decided by the kernel policy logs and CGWindowList, not
// by these API return values.

globalThis.__bcliRunWindowFocusTest = async (url) => {
  const result = { createResult: null, updateResult: null };
  try {
    const created = await chrome.windows.create({ url: url, focused: true });
    result.createResult = {
      ok: true,
      windowId: created && created.id !== undefined ? created.id : null,
      tabIds: created && created.tabs ? created.tabs.map((tab) => tab.id) : [],
      focusedFlag: created ? !!created.focused : null,
      windowType: created ? created.type : null,
    };
    if (created && created.id !== undefined) {
      try {
        const updated = await chrome.windows.update(created.id, { focused: true });
        result.updateResult = {
          ok: true,
          windowId: updated && updated.id !== undefined ? updated.id : null,
          focusedFlag: updated ? !!updated.focused : null,
        };
      } catch (e) {
        result.updateResult = { ok: false, error: String((e && e.message) || e) };
      }
    } else {
      result.updateResult = { ok: false, error: 'no window id from create' };
    }
  } catch (e) {
    result.createResult =
      result.createResult || { ok: false, error: String((e && e.message) || e) };
  }
  return result;
};
