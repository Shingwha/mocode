var MoCode = MoCode || {};

MoCode.Api = (function () {
  async function request(url, options) {
    var res = await fetch(url, options);
    return res;
  }

  function chat(message) {
    return request('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: message }),
    });
  }

  function interrupt() {
    return request('/api/interrupt', { method: 'POST' });
  }

  async function getStatus() {
    var res = await request('/api/status');
    return res.ok ? await res.json() : null;
  }

  async function listSessions() {
    var res = await request('/api/sessions');
    return res.ok ? await res.json() : { sessions: [] };
  }

  async function loadSession(id) {
    var res = await request('/api/sessions/' + encodeURIComponent(id));
    return res.ok ? await res.json() : null;
  }

  async function saveSession() {
    var res = await request('/api/sessions', { method: 'POST' });
    return res.ok ? await res.json() : null;
  }

  function deleteSession(id) {
    return request('/api/sessions/' + encodeURIComponent(id), { method: 'DELETE' });
  }

  function clearHistory() {
    return request('/api/history', { method: 'DELETE' });
  }

  function resolvePermission(id, response) {
    return request('/api/permission/' + id, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ response: response }),
    });
  }

  async function* parseSSE(response) {
    var reader = response.body.getReader();
    var decoder = new TextDecoder();
    var buffer = '';
    var eventType = '';

    while (true) {
      var result = await reader.read();
      if (result.done) break;

      buffer += decoder.decode(result.value, { stream: true });
      var lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (var i = 0; i < lines.length; i++) {
        var line = lines[i];
        if (line.startsWith('event: ')) {
          eventType = line.slice(7).trim();
        } else if (line.startsWith('data: ')) {
          var dataStr = line.slice(6);
          var data;
          try {
            data = JSON.parse(dataStr);
          } catch (_) {
            data = dataStr;
          }
          yield { type: eventType, data: data };
          eventType = '';
        }
      }
    }
  }

  return {
    chat: chat,
    interrupt: interrupt,
    getStatus: getStatus,
    listSessions: listSessions,
    loadSession: loadSession,
    saveSession: saveSession,
    deleteSession: deleteSession,
    clearHistory: clearHistory,
    resolvePermission: resolvePermission,
    parseSSE: parseSSE,
  };
})();
