var MoCode = MoCode || {};

MoCode.ToolCards = (function () {
  var containerEl;
  var cards = {};

  function escapeHtml(s) {
    return MoCode.Utils.escapeHtml(s);
  }

  function formatArgs(args) {
    if (!args || typeof args !== 'object') return '';
    try {
      return JSON.stringify(args, null, 2);
    } catch (e) {
      var keys = Object.keys(args);
      if (keys.length === 0) return '';
      return keys.map(function (k) {
        var v = args[k];
        return k + ': ' + (typeof v === 'string' ? v : JSON.stringify(v));
      }).join('\n');
    }
  }

  function getSummary(name, args) {
    if (!args || typeof args !== 'object' || Object.keys(args).length === 0) return '';
    var keys = Object.keys(args);
    var primaryArg = args['command'] || args['path'] || args['file_path'] || args['pattern'] || args['query'] || args[keys[0]];
    var str = (typeof primaryArg === 'string' ? primaryArg : JSON.stringify(primaryArg));
    if (str.length > 80) str = str.slice(0, 77) + '...';
    return str;
  }

  // Tool icons
  var ICONS = {
    read: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
    write: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
    edit: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
    append: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
    bash: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>',
    glob: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
    grep: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
    fetch: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>',
    sub_agent: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="5" r="3"/><circle cx="6" cy="19" r="2.5"/><circle cx="18" cy="19" r="2.5"/><path d="M12 8L6 16.5M12 8L18 16.5"/></svg>',
    compact: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><line x1="14" y1="10" x2="21" y2="3"/><line x1="3" y1="21" x2="10" y2="14"/></svg>',
    skill: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
  };

  var DEFAULT_ICON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>';

  function getIcon(name) {
    return ICONS[name] || DEFAULT_ICON;
  }

  var SPINNER_SVG = '<svg class="tool-spinner" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M12 2a10 10 0 0 1 10 10" /></svg>';
  var CHECK_SVG = '<svg class="tool-check" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';

  // --- ToolCard class ---

  function ToolCard(id, name, container) {
    this.id = id;
    this.name = name;
    this.state = 'running';
    this.el = document.createElement('div');
    this.el.className = 'tool-card';
    this.el.dataset.state = 'running';
    this.el.id = id;
    this.el.innerHTML =
      '<div class="tool-header">' +
        '<span class="tool-icon">' + getIcon(name) + '</span>' +
        '<span class="tool-name">' + escapeHtml(name) + '</span>' +
        '<span class="tool-summary"></span>' +
        '<span class="tool-status">' + SPINNER_SVG + '</span>' +
        '<svg class="tool-toggle" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>' +
      '</div>' +
      '<div class="tool-detail">' +
        '<pre class="tool-args"></pre>' +
        '<div class="tool-divider"></div>' +
        '<pre class="tool-result"></pre>' +
      '</div>';
    container.appendChild(this.el);
    MoCode.Messages.scrollToBottom();
  }

  ToolCard.prototype.setArgs = function (args) {
    var summary = getSummary(this.name, args);
    var summaryEl = this.el.querySelector('.tool-summary');
    if (summaryEl && summary) {
      summaryEl.textContent = summary;
    }
    var argsEl = this.el.querySelector('.tool-args');
    var text = formatArgs(args);
    if (argsEl && text) {
      argsEl.textContent = text;
    }
  };

  ToolCard.prototype.setResult = function (result) {
    var truncated = result.length > 300 ? result.slice(0, 300) : result;
    var isLong = result.length > 300;
    var resultEl = this.el.querySelector('.tool-result');
    if (resultEl) {
      resultEl.textContent = truncated;
      if (isLong) {
        var btn = document.createElement('button');
        btn.className = 'expand-output-btn';
        btn.textContent = 'Show full output (' + result.length + ' chars)';
        var fullPre = document.createElement('pre');
        fullPre.className = 'full-output';
        fullPre.textContent = result;
        resultEl.appendChild(btn);
        resultEl.appendChild(fullPre);
      }
    }
    this.el.dataset.state = 'complete';
    this.state = 'complete';
    var statusEl = this.el.querySelector('.tool-status');
    if (statusEl) statusEl.innerHTML = CHECK_SVG;
    this.el.classList.remove('expanded');
    MoCode.Messages.scrollToBottom();
  };

  ToolCard.prototype.updateId = function (newId) {
    delete cards[this.id];
    this.id = newId;
    this.el.id = newId;
    cards[newId] = this;
  };

  ToolCard.prototype.destroy = function () {
    if (this.el.parentNode) this.el.parentNode.removeChild(this.el);
    delete cards[this.id];
  };

  // --- Module-level event handling ---

  function handleEvent(type, data) {
    switch (type) {
      case 'tool_start':
        handleToolStart(data);
        break;
      case 'tool_complete':
        handleToolComplete(data);
        break;
    }
  }

  function handleToolStart(data) {
    var card = new ToolCard('tool-' + data.id, data.name, containerEl);
    card.setArgs(data.args);
    card.el.classList.add('expanded');
    cards[card.id] = card;
  }

  function handleToolComplete(data) {
    var card = cards['tool-' + data.id];
    if (!card) {
      createFromHistory(data.name || 'output', data.result || '');
      return;
    }
    card.setResult(data.result || '');
  }

  // --- Session restore ---

  function createFromHistory(name, result, args) {
    var card = new ToolCard('hist-' + name + '-' + Date.now(), name, containerEl);
    card.el.dataset.state = 'complete';
    card.state = 'complete';
    var statusEl = card.el.querySelector('.tool-status');
    if (statusEl) statusEl.innerHTML = CHECK_SVG;

    if (args) {
      card.setArgs(args);
    }

    var truncated = result.length > 300 ? result.slice(0, 300) : result;
    var isLong = result.length > 300;
    var resultEl = card.el.querySelector('.tool-result');
    if (resultEl) {
      resultEl.textContent = truncated;
      if (isLong) {
        var btn = document.createElement('button');
        btn.className = 'expand-output-btn';
        btn.textContent = 'Show full output (' + result.length + ' chars)';
        var fullPre = document.createElement('pre');
        fullPre.className = 'full-output';
        fullPre.textContent = result;
        resultEl.appendChild(btn);
        resultEl.appendChild(fullPre);
      }
    }
    cards[card.id] = card;
  }

  // --- Init and clear ---

  function init(el) {
    containerEl = el;
    el.addEventListener('click', function (e) {
      var header = e.target.closest('.tool-header');
      if (header) {
        var cardEl = header.parentElement;
        if (cardEl && cardEl.classList.contains('tool-card')) {
          cardEl.classList.toggle('expanded');
        }
      }
      var expandBtn = e.target.closest('.expand-output-btn');
      if (expandBtn) {
        var wrapper = expandBtn.closest('.tool-result');
        if (wrapper) {
          wrapper.classList.add('expanded');
          expandBtn.remove();
        }
      }
    });
  }

  function clear() {
    var ids = Object.keys(cards);
    for (var i = 0; i < ids.length; i++) {
      var card = cards[ids[i]];
      if (card.el.parentNode) card.el.parentNode.removeChild(card.el);
    }
    cards = {};
  }

  return {
    init: init,
    handleEvent: handleEvent,
    createFromHistory: createFromHistory,
    clear: clear,
  };
})();
