/* Ask KOKO: chat with Claude, which answers from KOKO's own data via tools. Uses map/selection/esc from app.js. */
"use strict";

const chatLog = document.getElementById("chat-log");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const chatSend = document.getElementById("chat-send");
const chatHistory = [];  // [{role, content}] plain-text turns sent back to the server each time
const lookedUp = L.layerGroup().addTo(map);  // places the assistant checked

// ---------- tabs ----------
const tabs = { report: document.getElementById("tab-report"), chat: document.getElementById("tab-chat") };
const panes = { report: document.getElementById("panel"), chat: document.getElementById("chat") };
function showTab(name) {
  for (const k of Object.keys(tabs)) {
    tabs[k].setAttribute("aria-selected", String(k === name));
    panes[k].hidden = k !== name;
  }
  if (name === "chat") chatInput.focus();
}
tabs.report.addEventListener("click", () => showTab("report"));
tabs.chat.addEventListener("click", () => showTab("chat"));

fetch("/chat/status").then((r) => r.json()).then((s) => {
  if (!s.configured) {
    addBubble("bot error", "Ask KOKO is not set up yet: add ANTHROPIC_API_KEY to the .env file and restart the server.");
  }
}).catch(() => {});

// ---------- rendering ----------
function addBubble(cls, text) {
  const div = document.createElement("div");
  div.className = `msg ${cls}`;
  if (text) div.textContent = text;
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
  return div;
}

function addToolNote(name, input) {
  const div = document.createElement("div");
  div.className = "tool-note";
  if (name === "search_address") {
    div.innerHTML = `🔎 Searching address <b>${esc(input?.query)}</b>`;
  } else if (name === "get_risk_report") {
    div.innerHTML = `📊 Checking official hazard data at <b>${(+input?.lat).toFixed(4)}, ${(+input?.lon).toFixed(4)}</b>`;
    if (Number.isFinite(+input?.lat) && Number.isFinite(+input?.lon)) {
      L.circleMarker([+input.lat, +input.lon], { radius: 8, color: "#7c3aed", weight: 3, fillOpacity: 0.15 })
        .bindTooltip("Checked by Ask KOKO").addTo(lookedUp);
    }
  } else {
    div.textContent = `⚙️ ${name}`;
  }
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
}

/* Small, safe Markdown subset: paragraphs, bullet lists, **bold**. Everything is escaped first. */
function renderMarkdown(src) {
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
  const out = [];
  let list = null;
  for (const line of src.split("\n")) {
    const m = line.match(/^\s*(?:[-*•]|\d+\.)\s+(.*)$/);
    if (m) {
      if (!list) { list = []; out.push(list); }
      list.push(`<li>${inline(m[1])}</li>`);
    } else {
      list = null;
      const t = line.replace(/^#+\s*/, "");
      if (t.trim()) out.push(`<p>${inline(t)}</p>`);
    }
  }
  return out.map((x) => (Array.isArray(x) ? `<ul>${x.join("")}</ul>` : x)).join("");
}

// ---------- sending ----------
async function send(question) {
  chatHistory.push({ role: "user", content: question });
  addBubble("user", question);
  chatSend.disabled = true;
  let bubble = addBubble("bot typing", "");
  let text = "";      // text of the current bubble
  let turnText = "";  // the whole answer for this turn, across bubbles

  const placeLookups = [];
  try {
    const res = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: chatHistory, selection }),
    });
    if (!res.ok) throw new Error(`Server error ${res.status}`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, i);
        buf = buf.slice(i + 2);
        if (!chunk.startsWith("data: ")) continue;
        const ev = JSON.parse(chunk.slice(6));
        if (ev.type === "text") {
          text += ev.text;
          turnText += ev.text;
          bubble.innerHTML = renderMarkdown(text);
        } else if (ev.type === "tool") {
          if (!text.trim()) bubble.remove();
          addToolNote(ev.name, ev.input);
          if (ev.name === "get_risk_report") placeLookups.push([+ev.input.lat, +ev.input.lon]);
          bubble = addBubble("bot typing", "");
          text = "";
        } else if (ev.type === "error") {
          addBubble("bot error", ev.message);
        }
        chatLog.scrollTop = chatLog.scrollHeight;
      }
    }
  } catch (err) {
    addBubble("bot error", `Could not reach Ask KOKO: ${err.message}`);
  } finally {
    bubble.classList.remove("typing");
    if (!text.trim()) bubble.remove();
    chatSend.disabled = false;
  }

  // keep the visible answer in history; tool calls and results stay server-side
  const answer = turnText.trim();
  if (answer) chatHistory.push({ role: "assistant", content: answer });
  else chatHistory.pop();  // failed turn: drop the question so the next request stays valid

  if (placeLookups.length > 1) map.fitBounds(L.latLngBounds(placeLookups).pad(0.3));
  else if (placeLookups.length === 1) map.flyTo(placeLookups[0], Math.max(map.getZoom(), 14));
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = chatInput.value.trim();
  if (!q || chatSend.disabled) return;
  chatInput.value = "";
  send(q);
});
chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); chatForm.requestSubmit(); }
});
document.querySelectorAll(".suggest [data-q]").forEach((b) =>
  b.addEventListener("click", () => { if (!chatSend.disabled) send(b.dataset.q); }));
