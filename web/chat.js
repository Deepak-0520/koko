/* Ask KOKO: chat with Claude, which answers from KOKO's own data via tools. Uses map/selection/esc/t from app.js and i18n.js.
   When the interface language changes, finished answers are translated (once per language, then cached),
   and tool notes and error messages re-render from their stored data. */
"use strict";

const chatLog = document.getElementById("chat-log");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const chatSend = document.getElementById("chat-send");
const chatHistory = [];  // [{role, content}] plain-text turns sent back to the server each time (originals)
const answers = [];      // [{el, orig, origLang, cache: {lang: text}}] finished answers, for the language switch
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
  if (!s.configured) addError("no_key");
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

/* Errors keep their code (or a translatable key + vars) so they follow the language switch. */
function addError(code, message) {
  const div = addBubble("bot error", "");
  div.dataset.errCode = code || "";
  div.dataset.errMsg = message || "";
  renderError(div);
  return div;
}
function renderError(div) {
  const { errCode, errMsg } = div.dataset;
  div.textContent = errCode === "unreachable" ? t("chat.unreachable", { msg: errMsg })
    : errCode ? t(`chat.err.${errCode}`) : errMsg;
}

function addToolNote(name, input) {
  const div = document.createElement("div");
  div.className = "tool-note";
  div.dataset.tool = name;
  div.dataset.input = JSON.stringify(input ?? {});
  renderToolNote(div);
  if (name === "get_risk_report" && Number.isFinite(+input?.lat) && Number.isFinite(+input?.lon)) {
    L.circleMarker([+input.lat, +input.lon], { radius: 8, color: "#7c3aed", weight: 3, fillOpacity: 0.15 })
      .bindTooltip(() => t("map.checkedByAi")).addTo(lookedUp);
  }
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
}
function renderToolNote(div) {
  const name = div.dataset.tool, input = JSON.parse(div.dataset.input);
  if (name === "search_address") div.innerHTML = t("chat.tool.search", { q: esc(input.query) });
  else if (name === "get_risk_report") div.innerHTML = t("chat.tool.report", { lat: (+input.lat).toFixed(4), lon: (+input.lon).toFixed(4) });
  else div.textContent = `⚙️ ${name}`;
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
      const s = line.replace(/^#+\s*/, "");
      if (s.trim()) out.push(`<p>${inline(s)}</p>`);
    }
  }
  return out.map((x) => (Array.isArray(x) ? `<ul>${x.join("")}</ul>` : x)).join("");
}

/* "ja" if the text is mostly Japanese, "en" if mostly Latin script, otherwise "other" (e.g. Nepali). */
function detectLang(text) {
  const letters = text.replace(/[\s\d\p{P}\p{S}]/gu, "");
  if (!letters) return "other";
  const ja = (letters.match(/[぀-ヿ㐀-鿿ｦ-ﾟ]/g) || []).length;
  const latin = (letters.match(/[A-Za-zÀ-ɏ]/g) || []).length;
  if (ja / letters.length > 0.25) return "ja";
  if (latin / letters.length > 0.6) return "en";
  return "other";
}

// ---------- answers and the language switch ----------
function showAnswer(a, text, translated) {
  a.el.innerHTML = renderMarkdown(text) + (translated ? `<p class="translated">${t("chat.translated")}</p>` : "");
}

async function localizeAnswer(a) {
  const target = LANG;
  if (a.origLang === "other" || a.origLang === target) { showAnswer(a, a.orig, false); return; }
  if (a.cache[target]) { showAnswer(a, a.cache[target], true); return; }
  a.el.innerHTML = renderMarkdown(a.orig) + `<p class="translated">${t("chat.translating")}</p>`;
  a.el.classList.add("translating");
  try {
    const r = await fetch("/chat/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: a.orig, lang: target }),
    });
    if (!r.ok) throw new Error(String(r.status));
    a.cache[target] = (await r.json()).text;
    if (LANG === target) showAnswer(a, a.cache[target], true);  // ignore if the user switched again meanwhile
  } catch {
    if (LANG === target) a.el.innerHTML = renderMarkdown(a.orig) + `<p class="translated">${t("chat.translateFailed")}</p>`;
  } finally {
    a.el.classList.remove("translating");
  }
}

document.addEventListener("langchange", () => {
  chatLog.querySelectorAll(".tool-note[data-tool]").forEach(renderToolNote);
  chatLog.querySelectorAll(".msg.error").forEach(renderError);
  answers.forEach(localizeAnswer);
});

// ---------- sending ----------
async function send(question) {
  chatHistory.push({ role: "user", content: question });
  addBubble("user", question);
  chatSend.disabled = true;
  const turnBubbles = [];
  let bubble = addBubble("bot typing", "");
  turnBubbles.push(bubble);
  let text = "";      // text of the current bubble
  let turnText = "";  // the whole answer for this turn, across bubbles

  const placeLookups = [];
  try {
    const res = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: chatHistory, selection, lang: LANG }),
    });
    if (!res.ok) throw new Error(t("report.serverError", { status: res.status }));
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
          turnBubbles.push(bubble);
          text = "";
        } else if (ev.type === "error") {
          addError(ev.code, ev.message);
        }
        chatLog.scrollTop = chatLog.scrollHeight;
      }
    }
  } catch (err) {
    addError("unreachable", err.message);
  } finally {
    bubble.classList.remove("typing");
    chatSend.disabled = false;
  }

  // keep the visible answer in history; tool calls and results stay server-side
  const answer = turnText.trim();
  turnBubbles.forEach((b) => b.remove());
  if (answer) {
    chatHistory.push({ role: "assistant", content: answer });
    // one bubble per answer (after this turn's lookup notes), so it can be translated as a whole
    const a = { el: addBubble("bot", ""), orig: answer, origLang: detectLang(answer), cache: {} };
    answers.push(a);
    showAnswer(a, answer, false);
  } else {
    chatHistory.pop();  // failed turn: drop the question so the next request stays valid
  }

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
