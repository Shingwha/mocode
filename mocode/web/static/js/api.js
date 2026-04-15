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

  async function getConfig() {
    var res = await request('/api/config');
    return res.ok ? await res.json() : null;
  }

  async function switchModel({model, provider}) {
    var res = await request('/api/config/model', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({model: model, provider: provider}),
    });
    return res.ok ? await res.json() : null;
  }

  async function switchProvider({provider, model}) {
    var res = await request('/api/config/provider', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({provider: provider, model: model}),
    });
    return res.ok ? await res.json() : null;
  }

  async function switchMode(mode) {
    var res = await request('/api/config/mode', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode: mode}),
    });
    return res.ok ? await res.json() : null;
  }

  async function addProvider({key, name, base_url, api_key, models}) {
    var res = await request('/api/config/providers', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key: key, name: name, base_url: base_url, api_key: api_key, models: models}),
    });
    return res.ok ? await res.json() : null;
  }

  async function updateProvider(key, {name, base_url, api_key}) {
    var res = await request('/api/config/providers/' + encodeURIComponent(key), {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name, base_url: base_url, api_key: api_key}),
    });
    return res.ok ? await res.json() : null;
  }

  async function removeProvider(key) {
    var res = await request('/api/config/providers/' + encodeURIComponent(key), {
      method: 'DELETE',
    });
    return res.ok ? await res.json() : null;
  }

  async function addModel(model, provider) {
    var res = await request('/api/config/models', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({model: model, provider: provider}),
    });
    return res.ok ? await res.json() : null;
  }

  async function removeModel(model, provider) {
    var res = await request('/api/config/models/' + encodeURIComponent(model), {
      method: 'DELETE',
    });
    return res.ok ? await res.json() : null;
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
    getConfig: getConfig,
    switchModel: switchModel,
    switchProvider: switchProvider,
    switchMode: switchMode,
    addProvider: addProvider,
    updateProvider: updateProvider,
    removeProvider: removeProvider,
    addModel: addModel,
    removeModel: removeModel,
    resolvePermission: resolvePermission,
    parseSSE: parseSSE,
  };
})();
