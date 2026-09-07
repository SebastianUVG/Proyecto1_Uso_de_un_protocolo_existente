"use strict";

const elements = {
  chatForm: document.querySelector("#chat-form"),
  input: document.querySelector("#message-input"),
  sendButton: document.querySelector("#send-button"),
  conversation: document.querySelector("#conversation"),
  messageList: document.querySelector("#message-list"),
  pendingArea: document.querySelector("#pending-area"),
  welcome: document.querySelector("#welcome-state"),
  typing: document.querySelector("#typing-indicator"),
  connection: document.querySelector("#connection-state"),
  serverList: document.querySelector("#server-list"),
  newChat: document.querySelector("#new-chat-button"),
  refreshStatus: document.querySelector("#refresh-status-button"),
  openLogs: document.querySelector("#open-logs-button"),
  closeLogs: document.querySelector("#close-logs-button"),
  refreshLogs: document.querySelector("#refresh-logs-button"),
  logsDrawer: document.querySelector("#logs-drawer"),
  logsBackdrop: document.querySelector("#logs-backdrop"),
  logsList: document.querySelector("#logs-list"),
  menuButton: document.querySelector("#menu-button"),
  sidebar: document.querySelector("#sidebar"),
  toastRegion: document.querySelector("#toast-region"),
};

const state = {
  messages: [],
  pending: null,
  busy: false,
};

document.addEventListener("DOMContentLoaded", initialize);

async function initialize() {
  bindEvents();
  setBusy(true);
  try {
    const snapshot = await api("/api/session", { method: "POST" });
    applySnapshot(snapshot);
    await refreshServerStatus();
    elements.input.focus();
  } catch (error) {
    setConnection(false, "Unavailable");
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

function bindEvents() {
  elements.chatForm.addEventListener("submit", sendMessage);
  elements.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      elements.chatForm.requestSubmit();
    }
  });
  elements.input.addEventListener("input", resizeComposer);
  elements.newChat.addEventListener("click", newChat);
  elements.refreshStatus.addEventListener("click", refreshServerStatus);
  elements.openLogs.addEventListener("click", openLogsDrawer);
  elements.closeLogs.addEventListener("click", closeLogsDrawer);
  elements.logsBackdrop.addEventListener("click", closeLogsDrawer);
  elements.refreshLogs.addEventListener("click", refreshLogs);
  elements.menuButton.addEventListener("click", toggleSidebar);
  document.querySelectorAll(".suggestion").forEach((button) => {
    button.addEventListener("click", () => {
      elements.input.value = button.textContent.trim();
      resizeComposer();
      elements.chatForm.requestSubmit();
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeLogsDrawer();
      closeSidebar();
    }
  });
}

async function sendMessage(event) {
  event.preventDefault();
  const message = elements.input.value.trim();
  if (!message || state.busy || state.pending) return;
  setBusy(true);
  try {
    const snapshot = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    elements.input.value = "";
    resizeComposer();
    applySnapshot(snapshot);
    await refreshServerStatus();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
    elements.input.focus();
  }
}

async function newChat() {
  if (state.busy) return;
  setBusy(true);
  try {
    const snapshot = await api("/api/session/new", { method: "POST" });
    applySnapshot(snapshot);
    showToast("A new conversation is ready.");
    closeSidebar();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
    elements.input.focus();
  }
}

async function resolveConfirmation(decision) {
  if (state.busy || !state.pending) return;
  setBusy(true);
  disableConfirmationButtons();
  try {
    const snapshot = await api(`/api/${decision}`, { method: "POST" });
    applySnapshot(snapshot);
    showToast(decision === "confirm" ? "Operation confirmed." : "Operation cancelled.");
    await refreshServerStatus();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

function applySnapshot(snapshot) {
  state.messages = Array.isArray(snapshot.messages) ? snapshot.messages : [];
  state.pending = snapshot.pending_confirmation || null;
  renderConversation();
}

function renderConversation() {
  elements.messageList.replaceChildren();
  elements.welcome.hidden = state.messages.length > 0;
  for (const message of state.messages) {
    elements.messageList.append(createMessage(message));
  }
  renderPendingConfirmation();
  scrollToLatest();
}

function createMessage(message) {
  const article = document.createElement("article");
  article.className = `message ${safeRole(message.role)}`;
  const label = document.createElement("div");
  label.className = "message-label";
  label.textContent = message.role === "user" ? "You" : message.role === "action" ? "Status" : "Assistant";
  const content = document.createElement("div");
  content.className = "message-content markdown";
  if (message.role === "assistant") {
    content.append(renderMarkdown(message.content));
  } else {
    const paragraph = document.createElement("p");
    paragraph.textContent = message.content;
    content.append(paragraph);
  }
  article.append(label, content);
  return article;
}

function renderPendingConfirmation() {
  elements.pendingArea.replaceChildren();
  if (!state.pending) return;
  const card = document.createElement("section");
  card.className = "confirmation-card";
  card.setAttribute("role", "alert");

  const titleRow = document.createElement("div");
  titleRow.className = "confirmation-title";
  const marker = document.createElement("span");
  marker.textContent = "!";
  marker.setAttribute("aria-hidden", "true");
  const title = document.createElement("h2");
  title.textContent = state.pending.title || "Inventory modification";
  titleRow.append(marker, title);
  card.append(titleRow);

  const description = document.createElement("p");
  description.className = "confirmation-description";
  description.textContent = state.pending.description || "Review this operation before it is sent to Inventory MCP.";
  card.append(description);

  for (const operation of state.pending.operations || []) {
    const details = document.createElement("div");
    details.className = "operation-details";
    const tool = document.createElement("div");
    tool.className = "operation-tool";
    tool.textContent = humanize(operation.tool);
    details.append(tool);
    const argumentsList = document.createElement("div");
    argumentsList.className = "argument-list";
    for (const [name, value] of Object.entries(operation.arguments || {})) {
      const item = document.createElement("div");
      item.className = "argument-item";
      const key = document.createElement("strong");
      key.textContent = humanize(name);
      const displayed = document.createElement("span");
      displayed.textContent = formatValue(value);
      item.append(key, displayed);
      argumentsList.append(item);
    }
    details.append(argumentsList);
    card.append(details);
  }

  const actions = document.createElement("div");
  actions.className = "confirmation-actions";
  const cancel = document.createElement("button");
  cancel.className = "cancel-button";
  cancel.type = "button";
  cancel.textContent = "Cancel";
  cancel.addEventListener("click", () => resolveConfirmation("cancel"));
  const confirm = document.createElement("button");
  confirm.className = "confirm-button";
  confirm.type = "button";
  confirm.textContent = "Confirm operation";
  confirm.addEventListener("click", () => resolveConfirmation("confirm"));
  actions.append(cancel, confirm);
  card.append(actions);
  elements.pendingArea.append(card);
  confirm.focus();
}

async function refreshServerStatus() {
  try {
    const response = await api("/api/status");
    renderServerStatus(response.servers || []);
    setConnection(Boolean(response.connected), response.connected ? "Connected" : "Attention required");
  } catch (error) {
    setConnection(false, "Unavailable");
    renderServerStatus([]);
  }
}

function renderServerStatus(servers) {
  elements.serverList.replaceChildren();
  if (!servers.length) {
    const note = document.createElement("p");
    note.className = "empty-note";
    note.textContent = "Server status unavailable.";
    elements.serverList.append(note);
    return;
  }
  for (const server of servers) {
    const row = document.createElement("div");
    row.className = "server-row";
    const dot = document.createElement("span");
    dot.className = `server-status ${server.state.toLowerCase()}`;
    dot.setAttribute("aria-hidden", "true");
    const name = document.createElement("span");
    name.className = "server-name";
    name.textContent = server.name;
    const meta = document.createElement("span");
    meta.className = "server-meta";
    meta.textContent = server.transport ? `${server.transport} · ${server.tool_count} tools` : "Not configured";
    const stateLabel = document.createElement("span");
    stateLabel.className = "server-state-label";
    stateLabel.textContent = server.state;
    row.append(dot, name, meta, stateLabel);
    elements.serverList.append(row);
  }
}

async function openLogsDrawer() {
  elements.logsBackdrop.hidden = false;
  elements.logsDrawer.classList.add("open");
  elements.logsDrawer.setAttribute("aria-hidden", "false");
  elements.closeLogs.focus();
  await refreshLogs();
}

function closeLogsDrawer() {
  if (!elements.logsDrawer.classList.contains("open")) return;
  elements.logsDrawer.classList.remove("open");
  elements.logsDrawer.setAttribute("aria-hidden", "true");
  elements.logsBackdrop.hidden = true;
  elements.openLogs.focus();
}

async function refreshLogs() {
  elements.logsList.replaceChildren();
  const loading = document.createElement("p");
  loading.className = "empty-note";
  loading.textContent = "Loading logs…";
  elements.logsList.append(loading);
  try {
    const response = await api("/api/logs?limit=75");
    renderLogs(response.logs || []);
  } catch (error) {
    elements.logsList.replaceChildren();
    const note = document.createElement("p");
    note.className = "empty-note";
    note.textContent = error.message;
    elements.logsList.append(note);
  }
}

function renderLogs(records) {
  elements.logsList.replaceChildren();
  if (!records.length) {
    const note = document.createElement("p");
    note.className = "empty-note";
    note.textContent = "No MCP interactions have been logged yet.";
    elements.logsList.append(note);
    return;
  }
  for (const record of records.slice().reverse()) {
    const details = document.createElement("details");
    details.className = "log-record";
    const summary = document.createElement("summary");
    const method = document.createElement("span");
    method.className = "log-method";
    method.textContent = record.method || "protocol message";
    const direction = document.createElement("span");
    direction.className = "log-direction";
    direction.textContent = record.direction || "unknown direction";
    const server = document.createElement("span");
    server.className = "log-server";
    server.textContent = `${record.server || "unknown"} · ${record.transport || "unknown"} · ID ${record.request_id ?? "—"}`;
    const timestamp = document.createElement("span");
    timestamp.className = "log-time";
    timestamp.textContent = formatTimestamp(record.timestamp);
    summary.append(method, direction, server, timestamp);
    const payload = document.createElement("pre");
    payload.className = "log-json";
    payload.textContent = JSON.stringify(record.message, null, 2);
    details.append(summary, payload);
    elements.logsList.append(details);
  }
}

function renderMarkdown(source) {
  const root = document.createElement("div");
  const lines = String(source || "").replace(/\r\n/g, "\n").split("\n");
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fence = line.match(/^\s*```([\w-]*)\s*$/);
    if (fence) {
      const codeLines = [];
      index += 1;
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      index += index < lines.length ? 1 : 0;
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      if (fence[1]) code.className = `language-${fence[1]}`;
      code.textContent = codeLines.join("\n");
      pre.append(code);
      root.append(pre);
      continue;
    }

    if (isTableStart(lines, index)) {
      const headers = splitTableRow(line);
      index += 2;
      const rows = [];
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        rows.push(splitTableRow(lines[index]));
        index += 1;
      }
      root.append(createTable(headers, rows));
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const element = document.createElement(`h${heading[1].length}`);
      appendInline(element, heading[2]);
      root.append(element);
      index += 1;
      continue;
    }

    const unordered = line.match(/^\s*[-*]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      const list = document.createElement(unordered ? "ul" : "ol");
      const matcher = unordered ? /^\s*[-*]\s+(.+)$/ : /^\s*\d+[.)]\s+(.+)$/;
      while (index < lines.length) {
        const itemMatch = lines[index].match(matcher);
        if (!itemMatch) break;
        const item = document.createElement("li");
        appendInline(item, itemMatch[1]);
        list.append(item);
        index += 1;
      }
      root.append(list);
      continue;
    }

    const paragraphLines = [line.trim()];
    index += 1;
    while (index < lines.length && lines[index].trim() && !isBlockStart(lines, index)) {
      paragraphLines.push(lines[index].trim());
      index += 1;
    }
    const paragraph = document.createElement("p");
    appendInline(paragraph, paragraphLines.join("\n"));
    root.append(paragraph);
  }
  return root;
}

function appendInline(parent, text) {
  const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|_[^_\n]+_)/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > cursor) parent.append(document.createTextNode(text.slice(cursor, match.index)));
    const token = match[0];
    let element;
    let content;
    if (token.startsWith("`")) {
      element = document.createElement("code");
      content = token.slice(1, -1);
    } else if (token.startsWith("**") || token.startsWith("__")) {
      element = document.createElement("strong");
      content = token.slice(2, -2);
    } else {
      element = document.createElement("em");
      content = token.slice(1, -1);
    }
    element.textContent = content;
    parent.append(element);
    cursor = match.index + token.length;
  }
  if (cursor < text.length) parent.append(document.createTextNode(text.slice(cursor)));
}

function isBlockStart(lines, index) {
  const line = lines[index];
  return /^\s*```/.test(line) || /^(#{1,6})\s+/.test(line) || /^\s*[-*]\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line) || isTableStart(lines, index);
}

function isTableStart(lines, index) {
  return index + 1 < lines.length && lines[index].includes("|") && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[index + 1]);
}

function splitTableRow(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function createTable(headers, rows) {
  const wrapper = document.createElement("div");
  wrapper.className = "table-wrap";
  const table = document.createElement("table");
  const head = document.createElement("thead");
  const headerRow = document.createElement("tr");
  headers.forEach((header) => {
    const cell = document.createElement("th");
    cell.scope = "col";
    appendInline(cell, header);
    headerRow.append(cell);
  });
  head.append(headerRow);
  const body = document.createElement("tbody");
  rows.forEach((row) => {
    const tableRow = document.createElement("tr");
    headers.forEach((_, column) => {
      const cell = document.createElement("td");
      appendInline(cell, row[column] || "");
      tableRow.append(cell);
    });
    body.append(tableRow);
  });
  table.append(head, body);
  wrapper.append(table);
  return wrapper;
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, { credentials: "same-origin", ...options });
  } catch (error) {
    throw new Error("Unable to reach the Web backend.");
  }
  let payload = null;
  try {
    payload = await response.json();
  } catch (error) {
    if (response.ok) throw new Error("The Web backend returned an invalid response.");
  }
  if (!response.ok) {
    throw new Error(payload?.detail || `Request failed with HTTP ${response.status}.`);
  }
  return payload;
}

function setBusy(busy) {
  state.busy = busy;
  elements.input.disabled = busy || Boolean(state.pending);
  elements.sendButton.disabled = busy || Boolean(state.pending);
  elements.newChat.disabled = busy;
  elements.typing.hidden = !busy;
  elements.conversation.setAttribute("aria-busy", String(busy));
  if (busy) scrollToLatest();
}

function disableConfirmationButtons() {
  elements.pendingArea.querySelectorAll("button").forEach((button) => {
    button.disabled = true;
  });
}

function setConnection(connected, label) {
  elements.connection.classList.toggle("connected", connected);
  elements.connection.classList.toggle("error", !connected);
  elements.connection.querySelector("span:last-child").textContent = label;
}

function resizeComposer() {
  elements.input.style.height = "auto";
  elements.input.style.height = `${Math.min(elements.input.scrollHeight, 160)}px`;
}

function scrollToLatest() {
  requestAnimationFrame(() => {
    elements.conversation.scrollTop = elements.conversation.scrollHeight;
  });
}

function showToast(message, isError = false) {
  const toast = document.createElement("div");
  toast.className = `toast${isError ? " error" : ""}`;
  toast.textContent = message;
  elements.toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), 5000);
}

function toggleSidebar() {
  const open = elements.sidebar.classList.toggle("open");
  elements.menuButton.setAttribute("aria-expanded", String(open));
}

function closeSidebar() {
  elements.sidebar.classList.remove("open");
  elements.menuButton.setAttribute("aria-expanded", "false");
}

function safeRole(role) {
  return ["user", "assistant", "action"].includes(role) ? role : "assistant";
}

function humanize(value) {
  return String(value || "").replace(/_/g, " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

function formatValue(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function formatTimestamp(value) {
  if (!value) return "Unknown time";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}
