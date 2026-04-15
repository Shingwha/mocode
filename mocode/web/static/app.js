const $ = s => document.querySelector(s);
const messagesEl = $('#messages');
const inputEl = $('#input');
const sendBtn = $('#send-btn');
const interruptBtn = $('#interrupt-btn');
const statusEl = $('#status');
const sessionListEl = $('#session-list');
const sidebarEl = $('#sidebar');
const sidebarToggle = $('#sidebar-toggle');
const emptyState = $('#empty-state');
const chatTitle = $('#chat-title');
const modelInfo = $('#model-info');
const newChatBtn = $('#new-chat-btn');

let busy = false;
let currentSessionId = null;
let sessions = [];
let pendingPermCards = {};
let inputCards = {};
let toolNames = {};
let lastApprovedPerm = null;
let deniedToolNames = new Set();
let suppressInterrupt = false;
let queuedMessage = null;

// --- Init ---

document.addEventListener('DOMContentLoaded', () => {
  loadSessions();
  fetchStatus();
  inputEl.focus();
});

// --- Sidebar Toggle ---

sidebarToggle.addEventListener('click', () => {
  sidebarEl.classList.toggle('collapsed');
});

// --- Input ---

inputEl.addEventListener('input', () => {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
});

inputEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});

sendBtn.addEventListener('click', send);
interruptBtn.addEventListener('click', interrupt);
newChatBtn.addEventListener('click', newChat);

// --- Status ---

function setBusy(val) {
  busy = val;
  sendBtn.disabled = val;
  interruptBtn.style.display = val ? 'flex' : 'none';
  sendBtn.style.display = val ? 'none' : 'flex';
  statusEl.className = val ? 'busy' : 'idle';
  statusEl.textContent = val ? 'thinking...' : '';
  if (val) {
    showTypingIndicator();
  } else {
    removeTypingIndicator();
  }
}

function showTypingIndicator() {
  removeTypingIndicator();
  const el = document.createElement('div');
  el.className = 'typing-indicator';
  el.id = 'typing-indicator';
  el.innerHTML = '<span class="dot"></span><span class="dot"></span><span class="dot"></span>';
  messagesEl.appendChild(el);
  scrollBottom();
}

function removeTypingIndicator() {
  const el = document.getElementById('typing-indicator');
  if (el) el.remove();
}

async function fetchStatus() {
  try {
    const res = await fetch('/api/status');
    if (res.ok) {
      const data = await res.json();
      modelInfo.textContent = data.model || '';
    }
  } catch {}
}

// --- Scroll ---

function scrollBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

// --- Escape ---

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// --- Format ---

function formatArgs(args) {
  if (!args || typeof args !== 'object') return '';
  const keys = Object.keys(args);
  if (keys.length === 0) return '';
  return keys.map(k => {
    const v = args[k];
    return k + ': ' + (typeof v === 'string' ? v : JSON.stringify(v));
  }).join('\n');
}

function formatTime(isoStr) {
  try {
    const d = new Date(isoStr);
    const now = new Date();
    const diffMs = now - d;
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return 'just now';
    if (diffMin < 60) return diffMin + 'm ago';
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return diffHr + 'h ago';
    const diffDay = Math.floor(diffHr / 24);
    if (diffDay < 7) return diffDay + 'd ago';
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  } catch {
    return '';
  }
}

function getSessionTitle(session) {
  for (const msg of session.messages || []) {
    if (msg.role === 'user' && msg.content) {
      const text = typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content);
      return text.slice(0, 40) + (text.length > 40 ? '...' : '');
    }
  }
  return session.id.replace(/^session_/, '').replace(/_/g, ' ');
}

// --- Empty State ---

function updateEmptyState() {
  const hasMessages = messagesEl.children.length > 0;
  if (hasMessages) {
    emptyState.classList.remove('visible');
    messagesEl.style.display = '';
  } else {
    emptyState.classList.add('visible');
    messagesEl.style.display = 'none';
  }
}

// ========== Session Management ==========

async function loadSessions() {
  try {
    const res = await fetch('/api/sessions');
    if (!res.ok) return;
    const data = await res.json();
    sessions = data.sessions || [];
    renderSessionList();
  } catch {}
}

function renderSessionList() {
  sessionListEl.innerHTML = '';
  for (const s of sessions) {
    const item = document.createElement('div');
    item.className = 'session-item' + (s.id === currentSessionId ? ' active' : '');
    item.dataset.id = s.id;

    const title = getSessionTitle(s);
    const meta = formatTime(s.updated_at || s.created_at) + (s.message_count > 0 ? ' \u00b7 ' + s.message_count + ' msgs' : '');

    item.innerHTML =
      '<div class="session-item-content">' +
        '<div class="session-item-title">' + escapeHtml(title) + '</div>' +
        '<div class="session-item-meta">' + escapeHtml(meta) + '</div>' +
      '</div>' +
      '<button class="session-item-delete" title="Delete session">&times;</button>';

    item.addEventListener('click', (e) => {
      if (e.target.closest('.session-item-delete')) return;
      switchSession(s.id);
    });

    item.querySelector('.session-item-delete').addEventListener('click', (e) => {
      e.stopPropagation();
      deleteSession(s.id);
    });

    sessionListEl.appendChild(item);
  }
}

async function switchSession(id) {
  if (busy) return;
  try {
    const res = await fetch('/api/sessions/' + encodeURIComponent(id));
    if (!res.ok) return;
    const session = await res.json();
    currentSessionId = session.id;
    clearChat();
    renderHistory(session.messages || []);
    chatTitle.textContent = getSessionTitle(session);
    highlightSession(id);
    updateEmptyState();
  } catch {}
}

function highlightSession(id) {
  document.querySelectorAll('.session-item').forEach(el => {
    el.classList.toggle('active', el.dataset.id === id);
  });
}

async function deleteSession(id) {
  if (busy) return;
  try {
    const res = await fetch('/api/sessions/' + encodeURIComponent(id), { method: 'DELETE' });
    if (!res.ok) return;
    sessions = sessions.filter(s => s.id !== id);
    if (currentSessionId === id) {
      currentSessionId = null;
      chatTitle.textContent = 'New Chat';
      await clearCurrentChat();
    }
    renderSessionList();
  } catch {}
}

async function newChat() {
  if (busy) return;
  try {
    await fetch('/api/history', { method: 'DELETE' });
  } catch {}
  currentSessionId = null;
  clearChat();
  chatTitle.textContent = 'New Chat';
  highlightSession(null);
  updateEmptyState();
  inputEl.focus();
}

async function clearCurrentChat() {
  try {
    await fetch('/api/history', { method: 'DELETE' });
  } catch {}
  clearChat();
  updateEmptyState();
}

function clearChat() {
  messagesEl.innerHTML = '';
  pendingPermCards = {};
  inputCards = {};
  toolNames = {};
  lastApprovedPerm = null;
  deniedToolNames = new Set();
}

async function autoSave() {
  try {
    const res = await fetch('/api/sessions', { method: 'POST' });
    if (!res.ok) return;
    const data = await res.json();
    const session = data.session;
    currentSessionId = session.id;
    chatTitle.textContent = getSessionTitle(session);
    await loadSessions();
  } catch {}
}

// ========== History Rendering ==========

function renderHistory(messages) {
  let i = 0;
  while (i < messages.length) {
    const msg = messages[i];
    if (msg.role === 'user') {
      addUserMessage(extractText(msg.content));
      i++;
    } else if (msg.role === 'assistant') {
      if (msg.content) {
        addAssistantMessage(extractText(msg.content));
      }
      i++;
    } else if (msg.role === 'tool') {
      const toolId = msg.tool_call_id || ('hist-tool-' + i);
      const toolName = msg.name || 'tool';
      const result = extractText(msg.content);
      const cardEl = createCard('complete', 'tool-' + toolId, toolName, '', false);
      const body = cardEl.querySelector('.card-body');
      if (body && result) {
        body.insertAdjacentHTML('beforeend',
          '<div class="card-section-result"><div class="card-section-label">Output</div>' + escapeHtml(result.length > 2000 ? result.slice(0, 2000) + '\n...(truncated)' : result) + '</div>'
        );
      }
      inputCards[toolId] = cardEl;
      i++;
    } else {
      i++;
    }
  }
  scrollBottom();
}

function extractText(content) {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content.map(c => {
      if (typeof c === 'string') return c;
      if (c.type === 'text') return c.text || '';
      return JSON.stringify(c);
    }).join('\n');
  }
  return JSON.stringify(content);
}

// ========== Message Renderers ==========

function addUserMessage(text) {
  const el = document.createElement('div');
  el.className = 'msg msg-user';
  el.innerHTML = '<div class="msg-bubble">' + escapeHtml(text) + '</div>';
  messagesEl.appendChild(el);
  scrollBottom();
  return el;
}

function addAssistantMessage(content) {
  removeTypingIndicator();
  const el = document.createElement('div');
  el.className = 'msg msg-assistant';
  el.innerHTML = '<div class="msg-bubble">' + escapeHtml(content) + '</div>';
  messagesEl.appendChild(el);
  scrollBottom();
  return el;
}

function addError(message) {
  removeTypingIndicator();
  const el = document.createElement('div');
  el.className = 'msg msg-error';
  el.innerHTML = '<div class="msg-bubble">' + escapeHtml(message) + '</div>';
  messagesEl.appendChild(el);
  scrollBottom();
}

function addInterrupted() {
  removeTypingIndicator();
  const el = document.createElement('div');
  el.className = 'msg msg-interrupted';
  el.innerHTML = '<div class="msg-bubble">Interrupted</div>';
  messagesEl.appendChild(el);
  scrollBottom();
}

// ========== Tool Cards ==========

function createCard(state, id, name, bodyHtml, expanded) {
  const el = document.createElement('div');
  el.className = 'tool-card' + (expanded ? ' expanded' : '');
  el.dataset.state = state;
  el.id = id;
  el.innerHTML =
    '<div class="card-header" onclick="toggleCard(this.parentElement)">' +
      '<span class="card-indicator"></span>' +
      '<span class="card-name">' + escapeHtml(name) + '</span>' +
      '<span class="card-toggle">&#9654;</span>' +
    '</div>' +
    '<div class="card-body">' + bodyHtml + '</div>';
  messagesEl.appendChild(el);
  scrollBottom();
  return el;
}

function setCardState(el, state) {
  el.dataset.state = state;
}

function addBadge(el, text, cls) {
  const header = el.querySelector('.card-header');
  const existing = header.querySelector('.card-badge');
  if (existing) existing.remove();
  const badge = document.createElement('span');
  badge.className = 'card-badge ' + cls;
  badge.textContent = text;
  header.insertBefore(badge, header.querySelector('.card-toggle'));
}

window.toggleCard = function(el) {
  if (!el || !el.classList) return;
  el.classList.toggle('expanded');
  if (el.classList.contains('expanded')) {
    requestAnimationFrame(() => {
      el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
  }
};

function addPermission(requestId, toolName, args, description) {
  const argsHtml = formatArgs(args)
    ? '<div class="card-section-args"><div class="card-section-label">Input</div>' + escapeHtml(formatArgs(args)) + '</div>'
    : (description ? '<div class="perm-desc">' + escapeHtml(description) + '</div>' : '');

  const el = createCard('pending', 'perm-' + requestId, toolName, argsHtml, true);

  const actionsEl = document.createElement('div');
  actionsEl.className = 'perm-actions';
  actionsEl.id = 'perm-actions-' + requestId;
  actionsEl.innerHTML =
    '<button class="btn-approve" onclick="resolvePermission(\'' + requestId + '\',\'allow\')">Approve</button>' +
    '<button class="btn-deny" onclick="resolvePermission(\'' + requestId + '\',\'deny\')">Deny</button>';
  el.after(actionsEl);

  pendingPermCards[requestId] = { el, toolName, args };
}

function addToolStart(id, name, args) {
  toolNames[id] = name;

  if (lastApprovedPerm) {
    const cardEl = lastApprovedPerm.cardEl;
    cardEl.id = 'tool-' + id;
    inputCards[id] = cardEl;
    lastApprovedPerm = null;
    return;
  }

  const argsHtml = formatArgs(args)
    ? '<div class="card-section-args"><div class="card-section-label">Input</div>' + escapeHtml(formatArgs(args)) + '</div>'
    : '';
  const el = createCard('running', 'tool-' + id, name, argsHtml, false);
  inputCards[id] = el;
}

function addToolResult(id, name, result) {
  const cardEl = inputCards[id];
  if (!cardEl) {
    const displayName = name || toolNames[id] || 'output';
    const truncated = result.length > 2000 ? result.slice(0, 2000) + '\n... (truncated)' : result;
    const bodyHtml = '<div class="card-section-result"><div class="card-section-label">Output</div>' + escapeHtml(truncated) + '</div>';
    createCard('complete', 'result-' + id, displayName, bodyHtml, false);
    return;
  }

  const indicator = cardEl.querySelector('.card-indicator');
  if (indicator) indicator.style.animation = 'none';

  setCardState(cardEl, 'complete');

  const truncated = result.length > 2000 ? result.slice(0, 2000) + '\n... (truncated)' : result;
  const body = cardEl.querySelector('.card-body');
  if (body) {
    body.insertAdjacentHTML('beforeend',
      '<div class="card-section-result"><div class="card-section-label">Output</div>' + escapeHtml(truncated) + '</div>'
    );
  }

  cardEl.classList.remove('expanded');
  scrollBottom();
}

function resolvePermissionCard(requestId, approved) {
  const info = pendingPermCards[requestId];
  if (!info) return;

  const cardEl = info.el;
  const actions = document.getElementById('perm-actions-' + requestId);
  if (actions) actions.remove();
  const desc = cardEl.querySelector('.perm-desc');
  if (desc) desc.remove();

  if (approved) {
    setCardState(cardEl, 'running');
    addBadge(cardEl, 'Approved', 'badge-approved');
    cardEl.classList.remove('expanded');
    lastApprovedPerm = { requestId, cardEl, toolName: info.toolName };
  } else {
    setCardState(cardEl, 'denied');
    addBadge(cardEl, 'Denied', 'badge-denied');
    deniedToolNames.add(info.toolName);
    suppressInterrupt = true;
    setTimeout(() => { suppressInterrupt = false; }, 2000);
  }

  delete pendingPermCards[requestId];
}

// ========== Permission Resolve ==========

window.resolvePermission = async function(requestId, response) {
  const actions = document.getElementById('perm-actions-' + requestId);
  if (!actions) return;
  try {
    const res = await fetch('/api/permission/' + requestId, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ response }),
    });
    if (!res.ok) {
      actions.innerHTML = '<span style="color:#991b1b;font-size:12px;">Error: ' + res.status + '</span>';
    }
  } catch (e) {
    actions.innerHTML = '<span style="color:#991b1b;font-size:12px;">Error: ' + escapeHtml(e.message) + '</span>';
  }
};

// ========== SSE Stream ==========

async function send() {
  const text = inputEl.value.trim();
  if (!text) return;

  inputEl.value = '';
  inputEl.style.height = 'auto';
  addUserMessage(text);

  emptyState.classList.remove('visible');
  messagesEl.style.display = '';

  if (busy) {
    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      });
      if (res.ok) {
        const data = await res.json().catch({});
        if (data.queued) {
          showQueuedHint();
        }
      }
    } catch {}
    return;
  }

  setBusy(true);

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'HTTP ' + res.status }));
      addError(err.error || err.message || 'HTTP ' + res.status);
      setBusy(false);
      return;
    }

    const ct = res.headers.get('content-type') || '';
    if (!ct.includes('text/event-stream')) {
      setBusy(false);
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      let eventType = '';
      for (const line of lines) {
        if (line.startsWith('event: ')) {
          eventType = line.slice(7).trim();
        } else if (line.startsWith('data: ')) {
          const dataStr = line.slice(6);
          let data;
          try { data = JSON.parse(dataStr); } catch { data = dataStr; }
          handleSSE(eventType, data);
          eventType = '';
        }
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') {
      addError(e.message);
    }
  } finally {
    setBusy(false);
    autoSave();
  }
}

function showQueuedHint() {
  const existing = document.getElementById('queued-hint');
  if (existing) existing.remove();
  const el = document.createElement('div');
  el.id = 'queued-hint';
  el.className = 'msg msg-assistant';
  el.innerHTML = '<div class="msg-bubble" style="color:#888;font-style:italic;font-size:13px;">Message queued, will be processed after current response...</div>';
  messagesEl.appendChild(el);
  scrollBottom();
  setTimeout(() => { if (el.parentNode) el.remove(); }, 3000);
}

function handleSSE(eventType, data) {
  switch (eventType) {
    case 'text_complete':
      addAssistantMessage(data.content || '');
      break;
    case 'tool_start':
      if (!deniedToolNames.has(data.name)) {
        addToolStart(data.id, data.name, data.args);
      }
      break;
    case 'tool_complete':
      if (!deniedToolNames.has(data.name)) {
        addToolResult(data.id, data.name, data.result || '');
      } else {
        deniedToolNames.delete(data.name);
      }
      break;
    case 'permission_ask':
      addPermission(data.request_id, data.tool_name || data.tool, data.args, data.description);
      break;
    case 'permission_resolved':
      resolvePermissionCard(data.request_id, data.approved);
      break;
    case 'done':
      break;
    case 'error':
      addError(data.message || 'Unknown error');
      break;
    case 'interrupted':
      if (!suppressInterrupt) addInterrupted();
      break;
  }
}

async function interrupt() {
  try {
    await fetch('/api/interrupt', { method: 'POST' });
  } catch {}
}
