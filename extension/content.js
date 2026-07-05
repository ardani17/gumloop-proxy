// content.js - Isolated world (content script context)
// Bridges between proxy (localhost WS) and MAIN world (injected.js)
// Uses CustomEvents to communicate across world boundary

(function() {
  'use strict';

  var proxyWS = null;

  function connectProxy() {
    try {
      proxyWS = new WebSocket('ws://127.0.0.1:8083');

      proxyWS.onopen = function() {
        console.log('[Gumloop Bridge] Connected to proxy');
        proxyWS.send(JSON.stringify({type: 'bridge-ready'}));
      };

      proxyWS.onclose = function() {
        proxyWS = null;
        setTimeout(connectProxy, 3000);
      };

      proxyWS.onmessage = function(event) {
        var msg = JSON.parse(event.data);
        if (msg.type === 'send-to-gumloop') {
          sendViaMainWorld(msg.payload, msg.requestId);
        }
      };
    } catch (e) {
      setTimeout(connectProxy, 3000);
    }
  }

  // Listen for responses from MAIN world (injected.js)
  window.addEventListener('gumloop-bridge-response', function(e) {
    var detail = e.detail;
    console.log('[Gumloop Bridge] Response:', detail.requestId, detail.error ? 'ERROR: ' + detail.error : 'OK (' + (detail.content||'').length + ' chars)');

    if (proxyWS && proxyWS.readyState === WebSocket.OPEN) {
      proxyWS.send(JSON.stringify({
        type: 'gumloop-response',
        requestId: detail.requestId,
        content: detail.content || '',
        usage: detail.usage,
        credits: detail.credits,
        error: detail.error
      }));
    }
  });

  // Listen for streaming deltas from MAIN world
  window.addEventListener('gumloop-bridge-delta', function(e) {
    var detail = e.detail;
    if (proxyWS && proxyWS.readyState === WebSocket.OPEN) {
      proxyWS.send(JSON.stringify({
        type: 'gumloop-delta',
        requestId: detail.requestId,
        delta: detail.delta
      }));
    }
  });

  // Listen for capture confirmation
  window.addEventListener('gumloop-captured', function() {
    console.log('[Gumloop Bridge] JWT captured (notified by MAIN world)');
    if (proxyWS && proxyWS.readyState === WebSocket.OPEN) {
      proxyWS.send(JSON.stringify({type: 'gumloop-captured'}));
    }
  });

  // Send message via MAIN world using CustomEvent
  function sendViaMainWorld(userMessage, requestId) {
    console.log('[Gumloop Bridge] Dispatching to MAIN world:', userMessage.substring(0, 80));

    // Dispatch event that injected.js listens for
    window.dispatchEvent(new CustomEvent('gumloop-bridge-send', {
      detail: {
        message: userMessage,
        requestId: requestId
      }
    }));
  }

  connectProxy();
  console.log('[Gumloop Bridge] Content script loaded');
})();
