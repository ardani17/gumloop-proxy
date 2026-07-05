// injected.js - MAIN world (page context)
// Auto-type approach: fills Gumloop's chat box and submits programmatically
// This lets Gumloop handle hCaptcha naturally while we control the message content

(function() {
  'use strict';

  if (window.__gumloopBridgeInstalled) {
    console.log('[Gumloop Bridge] Already installed, skipping');
    return;
  }
  window.__gumloopBridgeInstalled = true;

  var OrigWebSocket = window.WebSocket;

  // Track the latest Gumloop WebSocket response
  var currentRequestId = null;
  var responseParts = [];

  window.WebSocket = function WebSocket(url, protocols) {
    if (!(this instanceof WebSocket)) {
      return new OrigWebSocket(url, protocols);
    }
    var ws;
    try {
      if (arguments.length === 0) {
        ws = new OrigWebSocket();
      } else if (arguments.length === 1) {
        ws = new OrigWebSocket(url);
      } else {
        ws = new OrigWebSocket(url, protocols);
      }
    } catch(e) {
      throw e;
    }

    if (url && typeof url === 'string' && url.indexOf('ws.gumloop.com') !== -1) {
      console.log('[Gumloop Bridge] Intercepted WS:', url);

      // Capture JWT from outgoing messages
      var origSend = ws.send.bind(ws);
      ws.send = function(data) {
        try {
          var parsed = JSON.parse(data);
          if (parsed.type === 'start' && parsed.payload) {
            window.__gumloopCaptured = {
              jwt: parsed.payload.id_token,
              userId: parsed.payload.context && parsed.payload.context.message && parsed.payload.context.message.creator_id,
              gummieId: parsed.payload.context && parsed.payload.context.gummie_id,
              interactionId: parsed.payload.context && parsed.payload.context.interaction_id
            };
            // If we have a pending request, swap the message content
            if (currentRequestId && parsed.payload.context && parsed.payload.context.message) {
              console.log('[Gumloop Bridge] Swapping message content for request:', currentRequestId);
              parsed.payload.context.message.content = window.__pendingMessage || parsed.payload.context.message.content;
              data = JSON.stringify(parsed);
            }
            console.log('[Gumloop Bridge] ✅ Captured JWT');
            window.dispatchEvent(new CustomEvent('gumloop-captured'));
          }
        } catch(e) {}
        return origSend(data);
      };

      // Intercept responses
      ws.addEventListener('message', function(e) {
        try {
          var data = JSON.parse(e.data);
          if (!currentRequestId) return;

          if (data.type === 'error') {
            window.dispatchEvent(new CustomEvent('gumloop-bridge-response', {
              detail: {requestId: currentRequestId, error: data.errorMessage || 'Gumloop error'}
            }));
            currentRequestId = null;
            responseParts = [];
          } else if (data.type === 'text-delta') {
            responseParts.push(data.delta);
            window.dispatchEvent(new CustomEvent('gumloop-bridge-delta', {
              detail: {requestId: currentRequestId, delta: data.delta}
            }));
          } else if (data.type === 'finish' && data.final) {
            window.dispatchEvent(new CustomEvent('gumloop-bridge-response', {
              detail: {
                requestId: currentRequestId,
                content: responseParts.join(''),
                usage: data.usage,
                credits: data.credits
              }
            }));
            currentRequestId = null;
            responseParts = [];
          }
        } catch(err) {}
      });
    }
    return ws;
  };
  window.WebSocket.prototype = OrigWebSocket.prototype;
  window.WebSocket.CONNECTING = OrigWebSocket.CONNECTING;
  window.WebSocket.OPEN = OrigWebSocket.OPEN;
  window.WebSocket.CLOSING = OrigWebSocket.CLOSING;
  window.WebSocket.CLOSED = OrigWebSocket.CLOSED;

  // Listen for send requests from content script
  window.addEventListener('gumloop-bridge-send', function(e) {
    var detail = e.detail;
    var message = detail.message;
    var requestId = detail.requestId;

    console.log('[Gumloop Bridge] Auto-typing message:', message.substring(0, 80));

    // Set the pending message and request tracking
    window.__pendingMessage = message;
    currentRequestId = requestId;
    responseParts = [];

    // Find the ProseMirror editor
    var editor = document.querySelector('.ProseMirror');
    if (!editor) {
      // Try textarea as fallback
      editor = document.querySelector('textarea');
    }

    if (!editor) {
      window.dispatchEvent(new CustomEvent('gumloop-bridge-response', {
        detail: {requestId: requestId, error: 'Chat input box not found'}
      }));
      return;
    }

    // Focus the editor
    editor.focus();

    // Clear existing content and type new message
    // For ProseMirror, we need to set innerHTML or use execCommand
    if (editor.classList.contains('ProseMirror')) {
      // Clear existing content
      editor.innerHTML = '';

      // Use document.execCommand for ProseMirror compatibility
      editor.focus();
      setTimeout(function() {
        try {
          // Type the message
          document.execCommand('insertText', false, message);
        } catch(e) {
          // Fallback: set textContent and dispatch input event
          editor.textContent = message;
          editor.dispatchEvent(new InputEvent('input', {
            bubbles: true,
            cancelable: true,
            data: message,
            inputType: 'insertText'
          }));
        }

        // Wait a bit then click submit
        setTimeout(function() {
          // Find submit button
          var submitBtn = document.querySelector('button[aria-label="Submit prompt"]');
          if (!submitBtn) {
            // Try other selectors
            var btns = document.querySelectorAll('button');
            for (var i = btns.length - 1; i >= 0; i--) {
              var ariaLabel = btns[i].getAttribute('aria-label') || '';
              if (ariaLabel.indexOf('Submit') !== -1 || ariaLabel.indexOf('Send') !== -1) {
                submitBtn = btns[i];
                break;
              }
            }
          }

          if (submitBtn) {
            console.log('[Gumloop Bridge] Clicking submit button');
            submitBtn.click();
          } else {
            // Try pressing Enter
            console.log('[Gumloop Bridge] No submit button found, pressing Enter');
            editor.dispatchEvent(new KeyboardEvent('keydown', {
              key: 'Enter',
              code: 'Enter',
              keyCode: 13,
              which: 13,
              bubbles: true,
              cancelable: true
            }));
          }
        }, 200);
      }, 100);
    } else if (editor.tagName === 'TEXTAREA') {
      // Standard textarea
      editor.value = message;
      editor.dispatchEvent(new Event('input', {bubbles: true}));

      setTimeout(function() {
        var submitBtn = document.querySelector('button[aria-label="Submit prompt"]');
        if (submitBtn) {
          submitBtn.click();
        } else {
          editor.dispatchEvent(new KeyboardEvent('keydown', {
            key: 'Enter', code: 'Enter', keyCode: 13, which: 13,
            bubbles: true, cancelable: true
          }));
        }
      }, 200);
    }

    // Set timeout for response
    setTimeout(function() {
      if (currentRequestId === requestId) {
        window.dispatchEvent(new CustomEvent('gumloop-bridge-response', {
          detail: {requestId: requestId, error: 'Timeout waiting for Gumloop response'}
        }));
        currentRequestId = null;
        responseParts = [];
      }
    }, 120000);
  });

  console.log('[Gumloop Bridge] MAIN world interceptor installed (auto-type mode)');
})();
