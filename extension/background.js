// background.js - Service Worker
// Injects injected.js into MAIN world of Gumloop pages (bypasses CSP)

// Re-inject on navigation/tab update
chrome.tabs.onUpdated.addListener(function(tabId, changeInfo, tab) {
  if (changeInfo.status === 'loading' && tab.url && tab.url.includes('gumloop.com')) {
    chrome.scripting.executeScript({
      target: {tabId: tabId},
      files: ['injected.js'],
      world: 'MAIN',
      injectImmediately: true
    }).catch(function(e) {
      // Tab might not be ready yet, retry on next event
    });
  }
});

// Also inject on extension install
chrome.runtime.onInstalled.addListener(function() {
  chrome.tabs.query({url: 'https://www.gumloop.com/*'}, function(tabs) {
    tabs.forEach(function(tab) {
      chrome.scripting.executeScript({
        target: {tabId: tab.id},
        files: ['injected.js'],
        world: 'MAIN',
        injectImmediately: true
      }).catch(function(e) {});
    });
  });
});
