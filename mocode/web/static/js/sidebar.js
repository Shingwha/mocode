var MoCode = MoCode || {};

MoCode.Sidebar = (function () {
  var listEl;
  var callbacks = {};
  var sessions = [];

  function escapeHtml(s) {
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  function formatTime(isoStr) {
    try {
      var d = new Date(isoStr);
      var now = new Date();
      var diffMs = now - d;
      var diffMin = Math.floor(diffMs / 60000);
      if (diffMin < 1) return 'just now';
      if (diffMin < 60) return diffMin + 'm ago';
      var diffHr = Math.floor(diffMin / 60);
      if (diffHr < 24) return diffHr + 'h ago';
      var diffDay = Math.floor(diffHr / 24);
      if (diffDay < 7) return diffDay + 'd ago';
      return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    } catch (_) {
      return '';
    }
  }

  function getSessionTitle(session) {
    var messages = session.messages || [];
    for (var i = 0; i < messages.length; i++) {
      var msg = messages[i];
      if (msg.role === 'user' && msg.content) {
        var text = typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content);
        return text.slice(0, 40) + (text.length > 40 ? '...' : '');
      }
    }
    return session.id.replace(/^session_/, '').replace(/_/g, ' ');
  }

  function init(el, opts) {
    listEl = el;
    callbacks = opts || {};
  }

  async function load() {
    try {
      var data = await MoCode.Api.listSessions();
      sessions = data.sessions || [];
      render();
    } catch (_) {}
  }

  function render() {
    listEl.innerHTML = '';
    for (var i = 0; i < sessions.length; i++) {
      var s = sessions[i];
      var item = document.createElement('div');
      item.className = 'session-item';
      item.dataset.id = s.id;
      item.setAttribute('role', 'listitem');

      var title = getSessionTitle(s);
      var meta = formatTime(s.updated_at || s.created_at) +
        (s.message_count > 0 ? ' \u00b7 ' + s.message_count + ' msgs' : '');

      item.innerHTML =
        '<div class="session-item-content">' +
          '<div class="session-item-title">' + escapeHtml(title) + '</div>' +
          '<div class="session-item-meta">' + escapeHtml(meta) + '</div>' +
        '</div>' +
        '<button class="session-item-delete" title="Delete session" aria-label="Delete session">&times;</button>';

      (function (sessionId) {
        item.addEventListener('click', function (e) {
          if (e.target.closest('.session-item-delete')) return;
          if (callbacks.onSwitch) callbacks.onSwitch(sessionId);
        });

        item.querySelector('.session-item-delete').addEventListener('click', function (e) {
          e.stopPropagation();
          if (callbacks.onDelete) callbacks.onDelete(sessionId);
        });
      })(s.id);

      listEl.appendChild(item);
    }
  }

  function setActive(id) {
    var items = document.querySelectorAll('.session-item');
    for (var i = 0; i < items.length; i++) {
      items[i].classList.toggle('active', items[i].dataset.id === id);
    }
  }

  async function switchSession(id) {
    try {
      var session = await MoCode.Api.loadSession(id);
      if (!session) return;
      return session;
    } catch (_) {
      return null;
    }
  }

  async function deleteSession(id) {
    try {
      var res = await MoCode.Api.deleteSession(id);
      if (!res.ok) return false;
      sessions = sessions.filter(function (s) { return s.id !== id; });
      render();
      return true;
    } catch (_) {
      return false;
    }
  }

  function getSessionById(id) {
    for (var i = 0; i < sessions.length; i++) {
      if (sessions[i].id === id) return sessions[i];
    }
    return null;
  }

  return {
    init: init,
    load: load,
    render: render,
    setActive: setActive,
    switchSession: switchSession,
    deleteSession: deleteSession,
    getSessionTitle: getSessionTitle,
    getSessionById: getSessionById,
    get sessions() { return sessions; },
  };
})();
