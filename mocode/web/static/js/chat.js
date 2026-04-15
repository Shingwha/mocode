var MoCode = MoCode || {};

MoCode.Chat = (function () {
  var state = {
    busy: false,
    currentSessionId: null,
    suppressInterrupt: false,
  };

  var inputEl, sendBtn, interruptBtn, statusEl, emptyState, chatTitle, modelInfo;

  function init() {
    inputEl = document.getElementById('input');
    sendBtn = document.getElementById('send-btn');
    interruptBtn = document.getElementById('interrupt-btn');
    statusEl = document.getElementById('status');
    emptyState = document.getElementById('empty-state');
    chatTitle = document.getElementById('chat-title');
    modelInfo = document.getElementById('model-info');

    // Input auto-resize
    inputEl.addEventListener('input', function () {
      inputEl.style.height = 'auto';
      inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
    });

    // Enter to send
    inputEl.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        send();
      }
    });

    sendBtn.addEventListener('click', send);
    interruptBtn.addEventListener('click', interrupt);
  }

  function setBusy(val) {
    state.busy = val;
    sendBtn.disabled = val;
    interruptBtn.style.display = val ? 'flex' : 'none';
    sendBtn.style.display = val ? 'none' : 'flex';
    statusEl.className = val ? 'busy' : 'idle';
    statusEl.textContent = val ? 'thinking' : '';
    if (val) {
      MoCode.Messages.showTypingIndicator();
    } else {
      MoCode.Messages.removeTypingIndicator();
    }
  }

  async function send() {
    var text = inputEl.value.trim();
    if (!text) return;

    inputEl.value = '';
    inputEl.style.height = 'auto';
    MoCode.Messages.createUser(text);

    emptyState.classList.remove('visible');
    var messagesEl = document.getElementById('messages');
    if (messagesEl) messagesEl.style.display = '';

    // If busy, queue the message via non-streaming POST
    if (state.busy) {
      try {
        var res = await MoCode.Api.chat(text);
        if (res.ok) {
          var data = await res.json().catch(function () { return {}; });
          if (data.queued) {
            showQueuedHint();
          }
        }
      } catch (_) {}
      return;
    }

    setBusy(true);

    try {
      var res = await MoCode.Api.chat(text);

      if (!res.ok) {
        var err = await res.json().catch(function () { return { error: 'HTTP ' + res.status }; });
        MoCode.Messages.createError(err.error || err.message || 'HTTP ' + res.status);
        setBusy(false);
        return;
      }

      var ct = res.headers.get('content-type') || '';
      if (!ct.includes('text/event-stream')) {
        setBusy(false);
        return;
      }

      // Process SSE stream
      var events = MoCode.Api.parseSSE(res);
      var event;
      while (!(event = await events.next()).done) {
        handleSSE(event.value.type, event.value.data);
      }
    } catch (e) {
      if (e.name !== 'AbortError') {
        MoCode.Messages.createError(e.message);
      }
    } finally {
      setBusy(false);
      autoSave();
    }
  }

  function handleSSE(eventType, data) {
    switch (eventType) {
      case 'text_complete':
        MoCode.Messages.createAssistant(data.content || '');
        break;
      case 'tool_start':
      case 'tool_complete':
      case 'permission_ask':
      case 'permission_resolved':
        MoCode.ToolCards.handleEvent(eventType, data);
        if (eventType === 'permission_resolved' && !data.approved) {
          state.suppressInterrupt = true;
          setTimeout(function () { state.suppressInterrupt = false; }, 2000);
        }
        break;
      case 'done':
        break;
      case 'error':
        MoCode.Messages.createError(data.message || 'Unknown error');
        break;
      case 'interrupted':
        if (!state.suppressInterrupt) {
          MoCode.Messages.createInterrupted();
        }
        break;
    }
  }

  async function interrupt() {
    try {
      await MoCode.Api.interrupt();
    } catch (_) {}
  }

  async function autoSave() {
    try {
      var data = await MoCode.Api.saveSession();
      if (!data || !data.session) return;
      var session = data.session;
      state.currentSessionId = session.id;
      chatTitle.textContent = MoCode.Sidebar.getSessionTitle(session);
      await MoCode.Sidebar.load();
      MoCode.Sidebar.setActive(session.id);
    } catch (_) {}
  }

  async function newChat() {
    if (state.busy) return;
    try {
      await MoCode.Api.clearHistory();
    } catch (_) {}
    state.currentSessionId = null;
    MoCode.Messages.clear();
    MoCode.ToolCards.clear();
    chatTitle.textContent = 'New Chat';
    MoCode.Sidebar.setActive(null);
    MoCode.Messages.updateEmptyState(emptyState);
    inputEl.focus();
  }

  async function switchSession(id) {
    if (state.busy) return;
    var session = await MoCode.Sidebar.switchSession(id);
    if (!session) return;
    state.currentSessionId = session.id;
    MoCode.Messages.clear();
    MoCode.ToolCards.clear();
    MoCode.Messages.renderHistory(session.messages || []);
    chatTitle.textContent = MoCode.Sidebar.getSessionTitle(session);
    MoCode.Sidebar.setActive(id);
    MoCode.Messages.updateEmptyState(emptyState);
  }

  async function deleteSession(id) {
    if (state.busy) return;
    var deleted = await MoCode.Sidebar.deleteSession(id);
    if (!deleted) return;
    if (state.currentSessionId === id) {
      state.currentSessionId = null;
      chatTitle.textContent = 'New Chat';
      try { await MoCode.Api.clearHistory(); } catch (_) {}
      MoCode.Messages.clear();
      MoCode.ToolCards.clear();
      MoCode.Messages.updateEmptyState(emptyState);
    }
  }

  function showQueuedHint() {
    var existing = document.getElementById('queued-hint');
    if (existing) existing.remove();
    var el = document.createElement('div');
    el.id = 'queued-hint';
    el.className = 'msg msg-assistant';
    el.innerHTML = '<div class="msg-bubble" style="color:#a1a1aa;font-style:italic;font-size:13px;">Message queued, will be processed after current response...</div>';
    var messagesEl = document.getElementById('messages');
    if (messagesEl) messagesEl.appendChild(el);
    MoCode.Messages.scrollToBottom();
    setTimeout(function () { if (el.parentNode) el.remove(); }, 3000);
  }

  async function fetchStatus() {
    try {
      var data = await MoCode.Api.getStatus();
      if (data && modelInfo) {
        modelInfo.textContent = data.model || '';
      }
    } catch (_) {}
  }

  return {
    init: init,
    send: send,
    interrupt: interrupt,
    handleSSE: handleSSE,
    setBusy: setBusy,
    autoSave: autoSave,
    newChat: newChat,
    switchSession: switchSession,
    deleteSession: deleteSession,
    fetchStatus: fetchStatus,
    get busy() { return state.busy; },
  };
})();
