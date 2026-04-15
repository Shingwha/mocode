var MoCode = MoCode || {};

MoCode.ToolCards = (function () {
  var containerEl;
  var cards = {};       // id -> ToolCard (all cards by current ID)
  var pending = {};     // requestId -> ToolCard (permission cards awaiting resolution)
  var nameIndex = {};   // toolName -> ToolCard (correlates permission -> tool_start)

  // --- Helpers ---

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

  function generateTitle(toolName, args) {
    if (!args || typeof args !== 'object' || Object.keys(args).length === 0) {
      return toolName;
    }

    var keys = Object.keys(args);

    // 只有一个参数，直接显示该参数的值
    if (keys.length === 1) {
      var v = args[keys[0]];
      var valueStr = (typeof v === 'string' ? v : JSON.stringify(v));
      // 截断过长的值
      if (valueStr.length > 80) {
        valueStr = valueStr.slice(0, 77) + '...';
      }
      return toolName + ': ' + valueStr;
    }

    // 多个参数，优先显示 path 参数
    var pathKeys = ['path', 'file_path', 'filepath', 'filename', 'file'];
    for (var i = 0; i < pathKeys.length; i++) {
      if (args.hasOwnProperty(pathKeys[i])) {
        var pathVal = args[pathKeys[i]];
        var pathStr = (typeof pathVal === 'string' ? pathVal : JSON.stringify(pathVal));
        if (pathStr.length > 60) {
          pathStr = pathStr.slice(0, 57) + '...';
        }
        return toolName + ': ' + pathStr;
      }
    }

    // 没有 path 参数，显示第一个参数的值
    var firstKey = keys[0];
    var firstVal = args[firstKey];
    var firstStr = (typeof firstVal === 'string' ? firstVal : JSON.stringify(firstVal));
    if (firstStr.length > 60) {
      firstStr = firstStr.slice(0, 57) + '...';
    }
    return toolName + ': ' + firstStr;
  }

  // --- ToolCard class ---

  function ToolCard(id, name, container) {
    this.id = id;
    this.name = name;
    this.state = null;
    this.el = document.createElement('div');
    this.el.className = 'card tool-card';
    this.el.id = id;
    this.el.innerHTML =
      '<div class="card-header" tabindex="0">' +
        '<span class="card-title"></span>' +
        '<svg class="card-toggle" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>' +
      '</div>' +
      '<div class="card-body"></div>';
    container.appendChild(this.el);
    MoCode.Messages.scrollToBottom();
  }

  ToolCard.prototype.setTitle = function(title) {
    var titleEl = this.el.querySelector('.card-title');
    if (titleEl) {
      titleEl.textContent = title;
    }
  };

  ToolCard.prototype.setArgs = function (args) {
    // 更新标题显示参数摘要
    this.setTitle(generateTitle(this.name, args));

    var text = formatArgs(args);
    if (!text) return;
    var body = this.el.querySelector('.card-body');
    if (!body) return;
    if (body.querySelector('.card-section-args')) return;
    body.insertAdjacentHTML('beforeend',
      '<div class="card-section-args">' + escapeHtml(text) + '</div>');
  };

  ToolCard.prototype.showPermissionButtons = function (requestId) {
    var body = this.el.querySelector('.card-body');
    if (!body) return;

    var actionsEl = document.createElement('div');
    actionsEl.className = 'perm-actions';

    var approveBtn = document.createElement('button');
    approveBtn.className = 'btn-approve';
    approveBtn.textContent = 'Approve';
    approveBtn.addEventListener('click', function () {
      resolvePermission(requestId, 'allow');
    });

    var denyBtn = document.createElement('button');
    denyBtn.className = 'btn-deny';
    denyBtn.textContent = 'Deny';
    denyBtn.addEventListener('click', function () {
      resolvePermission(requestId, 'deny');
    });

    actionsEl.appendChild(approveBtn);
    actionsEl.appendChild(denyBtn);
    body.appendChild(actionsEl);
  };

  ToolCard.prototype.removePermissionButtons = function () {
    var actions = this.el.querySelector('.perm-actions');
    if (actions) actions.remove();
  };

  ToolCard.prototype.transitionTo = function (newState) {
    var valid = {
      pending: ['running', 'denied'],
      running: ['complete'],
    };
    var allowed = valid[this.state];
    if (!allowed || allowed.indexOf(newState) === -1) return;
    this.state = newState;
    this.el.dataset.state = newState;
    if (this.state !== 'running') {
      var indicator = this.el.querySelector('.card-indicator');
      if (indicator) indicator.style.animation = 'none';
    }
  };

  ToolCard.prototype.setResult = function (result) {
    var truncated = result.length > 500 ? result.slice(0, 500) : result;
    var isLong = result.length > 500;
    var body = this.el.querySelector('.card-body');
    if (body) {
      body.insertAdjacentHTML('beforeend',
        '<div class="card-section-result-divider"></div>' +
        '<div class="card-section-result">' +
        escapeHtml(truncated) +
        (isLong ? '<button class="expand-output-btn">Show full output (' + result.length + ' chars)</button>' +
          '<div class="full-output">' + escapeHtml(result) + '</div>' : '') +
        '</div>');
    }
    this.transitionTo('complete');
    this.el.classList.remove('expanded');
    MoCode.Messages.scrollToBottom();
  };

  ToolCard.prototype.updateId = function (newId) {
    delete cards[this.id];
    this.id = newId;
    this.el.id = newId;
    cards[newId] = this;
  };

  ToolCard.prototype.collapse = function () {
    this.el.classList.remove('expanded');
  };

  ToolCard.prototype.expand = function () {
    this.el.classList.add('expanded');
    requestAnimationFrame(function () {
      this.el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }.bind(this));
  };

  ToolCard.prototype.destroy = function () {
    if (this.el.parentNode) this.el.parentNode.removeChild(this.el);
    delete cards[this.id];
  };

  // --- Module-level event handling ---

  function handleEvent(type, data) {
    switch (type) {
      case 'permission_ask':
        handlePermissionAsk(data);
        break;
      case 'permission_resolved':
        handlePermissionResolved(data);
        break;
      case 'tool_start':
        handleToolStart(data);
        break;
      case 'tool_complete':
        handleToolComplete(data);
        break;
    }
  }

  function handlePermissionAsk(data) {
    var requestId = data.request_id;
    var toolName = data.tool_name || data.tool;
    var card = new ToolCard('perm-' + requestId, toolName, containerEl);
    card.state = 'pending';
    card.el.dataset.state = 'pending';
    card.el.classList.add('expanded');
    card.setArgs(data.args);
    card.showPermissionButtons(requestId);

    cards[card.id] = card;
    pending[requestId] = card;
    nameIndex[toolName] = card;
  }

  function handlePermissionResolved(data) {
    var card = pending[data.request_id];
    if (!card) return;

    card.removePermissionButtons();

    if (data.approved) {
      card.transitionTo('running');
      card.collapse();
    } else {
      card.transitionTo('denied');
    }

    delete pending[data.request_id];
  }

  function handleToolStart(data) {
    var existing = nameIndex[data.name];
    if (existing) {
      if (existing.state === 'running') {
        existing.updateId('tool-' + data.id);
        existing.setArgs(data.args);
        delete nameIndex[data.name];
        return;
      }
      if (existing.state === 'denied') {
        existing.updateId('tool-' + data.id);
        delete nameIndex[data.name];
        return;
      }
    }

    var card = new ToolCard('tool-' + data.id, data.name, containerEl);
    card.state = 'running';
    card.el.dataset.state = 'running';
    card.setArgs(data.args);
    cards[card.id] = card;
  }

  function handleToolComplete(data) {
    var card = cards['tool-' + data.id];

    if (!card) {
      createFromHistory(data.name || 'output', data.result || '');
      return;
    }

    if (card.state === 'denied') {
      return;
    }

    card.setResult(data.result || '');
  }

  // --- Permission resolution (API call) ---

  async function resolvePermission(requestId, response) {
    var card = pending[requestId];
    if (!card) return;
    var actions = card.el.querySelector('.perm-actions');
    if (!actions) return;
    try {
      var res = await MoCode.Api.resolvePermission(requestId, response);
      if (!res.ok) {
        actions.innerHTML = '<span style="color:var(--c-error-text);font-size:12px;">Error: ' + res.status + '</span>';
      }
    } catch (e) {
      actions.innerHTML = '<span style="color:var(--c-error-text);font-size:12px;">Error: ' + escapeHtml(e.message) + '</span>';
    }
  }

  // --- Session restore ---

  function createFromHistory(name, result, args) {
    var card = new ToolCard('hist-' + name + '-' + Date.now(), name, containerEl);
    card.state = 'complete';
    card.el.dataset.state = 'complete';
    // 设置标题（如果有参数）
    if (args) {
      card.setTitle(generateTitle(name, args));
    }
    var body = card.el.querySelector('.card-body');
    if (body) {
      var html = '';
      if (args) {
        var argsText = formatArgs(args);
        if (argsText) html += '<div class="card-section-args">' + escapeHtml(argsText) + '</div>';
      }
      html += '<div class="card-section-result-divider"></div>' +
              '<div class="card-section-result">' + escapeHtml(result) + '</div>';
      body.innerHTML = html;
    }
    cards[card.id] = card;
  }

  // --- Init and clear ---

  function init(el) {
    containerEl = el;
    el.addEventListener('click', function (e) {
      var header = e.target.closest('.card-header');
      if (header) {
        var cardEl = header.parentElement;
        if (cardEl && cardEl.classList.contains('tool-card')) {
          cardEl.classList.toggle('expanded');
          if (cardEl.classList.contains('expanded')) {
            requestAnimationFrame(function () {
              cardEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            });
          }
        }
      }
      var expandBtn = e.target.closest('.expand-output-btn');
      if (expandBtn) {
        var wrapper = expandBtn.closest('.card-section-result');
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
    pending = {};
    nameIndex = {};
  }

  return {
    init: init,
    handleEvent: handleEvent,
    createFromHistory: createFromHistory,
    clear: clear,
  };
})();
