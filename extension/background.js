/**
 * Sythion Browser Bridge - Background Service Worker
 * Establishes a persistent, auto-reconnecting WebSocket connection to the local Sythion backend.
 * Full SPA compatibility for YouTube, Google, TradingView, and modern web apps.
 *
 * AUTHENTICATION (protected runtime contract): every connection must present
 * the per-launch Sythion control token. Configure it in the extension popup
 * (Port + Token); it is stored only in chrome.storage.local on this machine.
 */

const DEFAULT_PORT = 8000;
let ws = null;
let reconnectTimer = null;
let pingInterval = null;

async function getBridgeConfig() {
  const stored = await chrome.storage.local.get(["sythionPort", "sythionToken"]);
  return {
    port: Number(stored.sythionPort) || DEFAULT_PORT,
    token: stored.sythionToken || "",
  };
}

async function connectToSythion() {
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
    return;
  }

  let config;
  try {
    config = await getBridgeConfig();
  } catch (err) {
    scheduleReconnect();
    return;
  }
  if (!config.token) {
    chrome.action.setBadgeText({ text: "KEY" });
    chrome.action.setBadgeBackgroundColor({ color: "#f59e0b" });
    scheduleReconnect();
    return;
  }

  try {
    // Loopback only; the per-launch token travels in a WebSocket subprotocol,
    // never in the URL where it would leak into logs. Mirrors the backend's
    // sythion-token.<base64url(token)> format. base64url keeps the raw token
    // out of the Sec-WebSocket-Protocol header value too.
    const tokenBytes = new TextEncoder().encode(config.token);
    let binary = '';
    for (const byte of tokenBytes) binary += String.fromCharCode(byte);
    const encoded = btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    const subprotocol = `sythion-token.${encoded}`;
    ws = new WebSocket(`ws://127.0.0.1:${config.port}/api/v1/browser/ws`, [subprotocol]);

    ws.onopen = () => {
      console.log("[Sythion Bridge] Connected to Sythion backend");
      chrome.action.setBadgeText({ text: "ON" });
      chrome.action.setBadgeBackgroundColor({ color: "#22c55e" });

      // Start ping heartbeat
      if (pingInterval) clearInterval(pingInterval);
      pingInterval = setInterval(() => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "ping" }));
        }
      }, 10000);

      // Send client info handshake
      ws.send(JSON.stringify({
        browser: "Chrome",
        userAgent: navigator.userAgent,
        platform: navigator.platform,
      }));
    };

    ws.onmessage = async (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "ping" || msg.type === "pong" || msg.type === "handshake_ok") {
          return;
        }
        if (msg.action) {
          handleCommand(msg);
        }
      } catch (err) {
        console.error("[Sythion Bridge] Error parsing message:", err);
      }
    };

    ws.onclose = () => {
      console.log("[Sythion Bridge] Disconnected from Sythion. Retrying in 2s...");
      chrome.action.setBadgeText({ text: "OFF" });
      chrome.action.setBadgeBackgroundColor({ color: "#ef4444" });
      if (pingInterval) clearInterval(pingInterval);
      ws = null;
      scheduleReconnect();
    };

    ws.onerror = (err) => {
      console.warn("[Sythion Bridge] WebSocket error:", err);
      ws?.close();
    };
  } catch (err) {
    console.error("[Sythion Bridge] Failed to initialize WebSocket:", err);
    scheduleReconnect();
  }
}

function scheduleReconnect() {
  if (!reconnectTimer) {
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connectToSythion();
    }, 2000);
  }
}

// Keepalive via alarms
chrome.alarms.create("sythion_keepalive", { periodInMinutes: 0.25 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "sythion_keepalive") {
    connectToSythion();
  }
});

// Reconnect on tab activity
chrome.tabs.onActivated.addListener(() => connectToSythion());
chrome.tabs.onUpdated.addListener(() => connectToSythion());

// Command dispatcher
async function handleCommand(req) {
  const { id, action, params } = req;
  let response = { id, result: null, error: null };

  try {
    switch (action) {
      case "get_tabs": {
        const tabs = await chrome.tabs.query({});
        const tabList = tabs.map((t) => ({
          id: t.id,
          window_id: t.windowId,
          index: t.index,
          title: t.title || "Untitled Tab",
          url: t.url || "",
          active: t.active,
          is_active: t.active,
          pinned: t.pinned,
          discarded: t.discarded,
          fav_icon_url: t.favIconUrl || "",
        }));
        response.result = { tabs: tabList, count: tabList.length };
        break;
      }

      case "switch_tab": {
        const tabId = params.tab_id;
        const tab = await chrome.tabs.get(tabId);
        await chrome.tabs.update(tabId, { active: true });
        if (tab.windowId) {
          await chrome.windows.update(tab.windowId, { focused: true });
        }
        response.result = { success: true, tab_id: tabId, title: tab.title };
        break;
      }

      case "open_tab": {
        const newTab = await chrome.tabs.create({
          url: params.url || "about:blank",
          active: params.active !== false,
        });
        response.result = { success: true, tab_id: newTab.id, url: newTab.url };
        break;
      }

      case "close_tab": {
        await chrome.tabs.remove(params.tab_id);
        response.result = { success: true, tab_id: params.tab_id };
        break;
      }

      case "read_page": {
        let targetTabId = params?.tab_id;
        if (!targetTabId) {
          const [activeTab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
          targetTabId = activeTab?.id;
        }
        if (!targetTabId) {
          const [anyActive] = await chrome.tabs.query({ active: true });
          targetTabId = anyActive?.id;
        }

        if (!targetTabId) {
          throw new Error("No active tab found to read");
        }

        const injectionResults = await chrome.scripting.executeScript({
          target: { tabId: targetTabId },
          func: () => {
            const title = document.title || "";
            const url = window.location.href;

            // Extract headings
            const headings = Array.from(document.querySelectorAll("h1, h2, h3"))
              .map((h) => ({ level: h.tagName.toLowerCase(), text: h.innerText.trim() }))
              .filter((h) => h.text.length > 0)
              .slice(0, 15);

            // Extract main text content
            const clone = document.body.cloneNode(true);
            const removeSelectors = ["script", "style", "noscript", "svg", "nav", "footer", "iframe", "aside"];
            removeSelectors.forEach((sel) => clone.querySelectorAll(sel).forEach((el) => el.remove()));
            const textContent = clone.innerText.replace(/\n{3,}/g, "\n\n").trim().slice(0, 3000);

            // Extract key interactive elements (inputs, buttons, primary video/article links)
            const interactive = [];

            // 1. Search inputs & textareas
            const inputs = Array.from(document.querySelectorAll("input, textarea, select")).slice(0, 15);
            inputs.forEach((inp) => {
              const id = inp.id ? `#${inp.id}` : "";
              const name = inp.name ? `[name="${inp.name}"]` : "";
              const type = inp.type || "text";
              const placeholder = inp.placeholder || "";
              const ariaLabel = inp.getAttribute("aria-label") || "";
              if (type !== "hidden") {
                interactive.push({
                  type: "input",
                  input_type: type,
                  selector: id || name || (inp.className ? `.${inp.className.split(" ")[0]}` : inp.tagName.toLowerCase()),
                  label: placeholder || ariaLabel || name || id || "input",
                });
              }
            });

            // 2. Action buttons
            const buttons = Array.from(document.querySelectorAll("button, [role='button'], a.btn, a.button, #search-icon-legacy")).slice(0, 15);
            buttons.forEach((btn) => {
              const text = btn.innerText.trim().replace(/\s+/g, " ");
              const ariaLabel = btn.getAttribute("aria-label") || "";
              const id = btn.id ? `#${btn.id}` : "";
              if (text || ariaLabel || id) {
                interactive.push({
                  type: "button",
                  label: text || ariaLabel || id,
                  selector: id || (btn.className ? `.${btn.className.split(" ")[0]}` : "button"),
                });
              }
            });

            // 3. Clickable item links (YouTube video titles, article links)
            const links = Array.from(document.querySelectorAll("a#video-title, a[href*='/watch?v='], a#thumbnail, h3 a, article a")).slice(0, 10);
            links.forEach((a) => {
              const text = (a.innerText || a.getAttribute("title") || a.getAttribute("aria-label") || "").trim().replace(/\s+/g, " ");
              const id = a.id ? `#${a.id}` : "";
              const href = a.getAttribute("href") || "";
              if (text && href) {
                interactive.push({
                  type: "link",
                  label: text.slice(0, 80),
                  selector: id ? `a${id}` : `a[href*='${href.split('&')[0]}']`,
                  href,
                });
              }
            });

            return {
              title,
              url,
              headings,
              text_snippet: textContent,
              interactive_elements: interactive.slice(0, 25),
            };
          },
        });

        response.result = injectionResults[0]?.result || {};
        break;
      }

      case "interact_page": {
        let targetTabId = params?.tab_id;
        if (!targetTabId) {
          const [activeTab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
          targetTabId = activeTab?.id;
        }
        if (!targetTabId) {
          const [anyActive] = await chrome.tabs.query({ active: true });
          targetTabId = anyActive?.id;
        }

        if (!targetTabId) {
          throw new Error("No active tab found to interact with");
        }

        const actionType = params.action_type || params.action;
        const selector = params.selector || "";
        const text = params.text || "";
        const key = params.key || "";

        const injectionResults = await chrome.scripting.executeScript({
          target: { tabId: targetTabId },
          func: (act, sel, txt, k) => {
            let elem = null;
            if (sel) {
              try {
                elem = document.querySelector(sel);
              } catch (e) {
                // Invalid selector syntax, fallback to text search
              }
            }
            if (!elem && sel) {
              const all = Array.from(document.querySelectorAll("button, a, input, textarea, [role='button'], ytd-video-renderer, article"));
              elem = all.find((el) => (el.innerText || el.value || el.getAttribute("aria-label") || el.placeholder || "").toLowerCase().includes(sel.toLowerCase()));
            }

            if (act !== "scroll" && act !== "press_key" && !elem) {
              return { success: false, error: `Element not found matching selector or text: '${sel}'` };
            }

            if (act === "click") {
              // If target is a container (e.g. ytd-video-renderer), drill down to the actual clickable anchor/button
              let targetElem = elem;
              if (elem && elem.tagName.toLowerCase() !== "a" && elem.tagName.toLowerCase() !== "button") {
                const innerLink = elem.querySelector("a#video-title, a#thumbnail, a[href], button");
                if (innerLink) {
                  targetElem = innerLink;
                }
              }

              targetElem.scrollIntoView({ behavior: "smooth", block: "center" });
              targetElem.focus();

              // Full pointer/mouse event simulation for SPAs
              targetElem.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, cancelable: true }));
              targetElem.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
              targetElem.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, cancelable: true }));
              targetElem.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true }));
              targetElem.click();

              const clickedText = (targetElem.innerText || targetElem.getAttribute("title") || targetElem.value || "").slice(0, 100);

              // If it's a direct anchor with href and SPA didn't navigate, follow href
              if (targetElem.tagName.toLowerCase() === "a" && targetElem.href && !targetElem.href.startsWith("javascript:")) {
                setTimeout(() => {
                  if (window.location.href !== targetElem.href) {
                    window.location.href = targetElem.href;
                  }
                }, 150);
              }

              return { success: true, action: "click", target: sel, text: clickedText };
            } else if (act === "type") {
              elem.scrollIntoView({ behavior: "smooth", block: "center" });
              elem.focus();
              elem.value = txt || "";
              
              // Full InputEvent sequence for React/Polymer/Lit frameworks
              elem.dispatchEvent(new InputEvent("input", { bubbles: true, cancelable: true, inputType: "insertText", data: txt }));
              elem.dispatchEvent(new Event("change", { bubbles: true }));

              // Handle Enter / Search Submission
              if (k === "Enter" || k === "enter") {
                elem.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true }));
                elem.dispatchEvent(new KeyboardEvent("keypress", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true }));
                elem.dispatchEvent(new KeyboardEvent("keyup", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true }));

                // In YouTube / Web forms: click the search button if present
                const searchBtn = document.querySelector("#search-icon-legacy, button#search-button, button[type='submit']");
                if (searchBtn) {
                  searchBtn.click();
                } else if (elem.form) {
                  elem.form.requestSubmit ? elem.form.requestSubmit() : elem.form.submit();
                }
              }
              return { success: true, action: "type", target: sel, typed_text: txt };
            } else if (act === "press_key") {
              const target = elem || document.activeElement || document.body;
              target.dispatchEvent(new KeyboardEvent("keydown", { key: k, code: k, keyCode: k === "Enter" ? 13 : 0, which: k === "Enter" ? 13 : 0, bubbles: true, cancelable: true }));
              target.dispatchEvent(new KeyboardEvent("keypress", { key: k, code: k, keyCode: k === "Enter" ? 13 : 0, which: k === "Enter" ? 13 : 0, bubbles: true, cancelable: true }));
              target.dispatchEvent(new KeyboardEvent("keyup", { key: k, code: k, keyCode: k === "Enter" ? 13 : 0, which: k === "Enter" ? 13 : 0, bubbles: true, cancelable: true }));

              if (k === "Enter" || k === "enter") {
                const searchBtn = document.querySelector("#search-icon-legacy, button#search-button, button[type='submit']");
                if (searchBtn) {
                  searchBtn.click();
                }
              }
              return { success: true, action: "press_key", key: k };
            } else if (act === "scroll") {
              const direction = (sel || "down").toLowerCase();
              window.scrollBy({ top: direction === "up" ? -600 : 600, behavior: "smooth" });
              return { success: true, action: "scroll", direction };
            }

            return { success: false, error: `Unsupported interaction action: ${act}` };
          },
          args: [actionType, selector, text, key],
        });

        response.result = injectionResults[0]?.result || {};
        break;
      }

      case "take_screenshot": {

        let windowId = params?.window_id;
        let tab = null;

        if (params?.tab_id) {
          tab = await chrome.tabs.get(params.tab_id);
          windowId = tab.windowId;
        } else {
          const [activeTab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
          tab = activeTab;
          windowId = activeTab ? activeTab.windowId : null;
        }

        if (!tab) {
          const [anyActive] = await chrome.tabs.query({ active: true });
          tab = anyActive;
          windowId = anyActive ? anyActive.windowId : null;
        }

        const dataUrl = await chrome.tabs.captureVisibleTab(windowId, { format: "png" });
        const base64Data = dataUrl.replace(/^data:image\/png;base64,/, "");

        response.result = {
          success: true,
          tab_id: tab ? tab.id : null,
          title: tab ? tab.title : "Active Tab",
          url: tab ? tab.url : "",
          image_base64: base64Data,
          data_url: dataUrl,
          format: "png",
        };
        break;
      }

      default:
        response.error = `Unsupported action: ${action}`;

    }
  } catch (err) {
    response.error = err.message || String(err);
  }

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(response));
  }
}

// Initial start
connectToSythion();
