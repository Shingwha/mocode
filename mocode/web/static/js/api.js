var MoCode = MoCode || {};

MoCode.Api = (function () {
  function rawRequest(url, options) {
    return fetch(url, options);
  }

  async function jsonRequest(url, options) {
    var res = await fetch(url, options);
    return res.ok ? await res.json() : null;
  }

  function chat(message) {
    return rawRequest('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: message }),
    });
  }

  function interrupt() {
    return rawRequest('/api/interrupt', { method: 'POST' });
  }

  function getStatus() {
    return jsonRequest('/api/status');
  }

  function listSessions() {
    return jsonRequest('/api/sessions').then(function (d) { return d || { sessions: [] }; });
  }

  function loadSession(id) {
    return jsonRequest('/api/sessions/' + encodeURIComponent(id));
  }

  function saveSession() {
    return jsonRequest('/api/sessions', { method: 'POST' });
  }

  function deleteSession(id) {
    return rawRequest('/api/sessions/' + encodeURIComponent(id), { method: 'DELETE' });
  }

  function clearHistory() {
    return rawRequest('/api/history', { method: 'DELETE' });
  }

  function getConfig() {
    return jsonRequest('/api/config');
  }

  function switchModel({model, provider}) {
    return jsonRequest('/api/config/model', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({model: model, provider: provider}),
    });
  }

  function switchProvider({provider, model}) {
    return jsonRequest('/api/config/provider', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({provider: provider, model: model}),
    });
  }

  function switchMode(mode) {
    return jsonRequest('/api/config/mode', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode: mode}),
    });
  }

  function addProvider({key, name, base_url, api_key, models}) {
    return jsonRequest('/api/config/providers', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key: key, name: name, base_url: base_url, api_key: api_key, models: models}),
    });
  }

  function updateProvider(key, {name, base_url, api_key, models}) {
    return jsonRequest('/api/config/providers/' + encodeURIComponent(key), {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name, base_url: base_url, api_key: api_key, models: models}),
    });
  }

  function removeProvider(key) {
    return jsonRequest('/api/config/providers/' + encodeURIComponent(key), {
      method: 'DELETE',
    });
  }

  function resolvePermission(id, response) {
    return rawRequest('/api/permission/' + id, {
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
          } catch (e) {
            MoCode.Utils.logError('parseSSE', e);
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
    getConfig: getConfig,
    switchModel: switchModel,
    switchProvider: switchProvider,
    switchMode: switchMode,
    addProvider: addProvider,
    updateProvider: updateProvider,
    removeProvider: removeProvider,
    resolvePermission: resolvePermission,
    parseSSE: parseSSE,
  };
})();
