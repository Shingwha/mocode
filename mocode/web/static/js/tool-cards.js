var MoCode = MoCode || {};

MoCode.ToolCards = (function () {
  var containerEl;
  var pendingPermCards = {};
  var inputCards = {};
  var approvedPermMap = {}; // toolName -> { cardEl, requestId }
  var deniedToolNames = {};

  function escapeHtml(s) {
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  function init(el) {
    containerEl = el;
    // Event delegation for card toggle
    el.addEventListener('click', function (e) {
      var header = e.target.closest('.card-header');
      if (header) {
        var card = header.parentElement;
        if (card && card.classList.contains('tool-card')) {
          toggleCard(card);
        }
      }
      // Event delegation for expand-output buttons
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

  function toggleCard(el) {
    if (!el || !el.classList) return;
    el.classList.toggle('expanded');
    if (el.classList.contains('expanded')) {
      requestAnimationFrame(function () {
        el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      });
    }
  }

  function formatArgs(args) {
    if (!args || typeof args !== 'object') return '';
    var keys = Object.keys(args);
    if (keys.length === 0) return '';
    return keys.map(function (k) {
      var v = args[k];
      return k + ': ' + (typeof v === 'string' ? v : JSON.stringify(v));
    }).join('\n');
  }

  function createCard(state, id, name, bodyHtml, expanded) {
    var el = document.createElement('div');
    el.className = 'tool-card' + (expanded ? ' expanded' : '');
    el.dataset.state = state;
    el.id = id;
    el.innerHTML =
      '<div class="card-header">' +
        '<span class="card-indicator"></span>' +
        '<span class="card-name">' + escapeHtml(name) + '</span>' +
        '<span class="card-toggle">&#9654;</span>' +
      '</div>' +
      '<div class="card-body">' + bodyHtml + '</div>';
    containerEl.appendChild(el);
    MoCode.Messages.scrollToBottom();
    return el;
  }

  function createFromHistory(name, result) {
    var bodyHtml = '<div class="card-section-result"><div class="card-section-label">Output</div>' +
      escapeHtml(result) + '</div>';
    createCard('complete', 'hist-' + name + '-' + Date.now(), name, bodyHtml, false);
  }

  function addPermission(requestId, toolName, args, description) {
    var argsHtml = formatArgs(args)
      ? '<div class="card-section-args"><div class="card-section-label">Input</div>' + escapeHtml(formatArgs(args)) + '</div>'
      : (description ? '<div class="perm-desc">' + escapeHtml(description) + '</div>' : '');

    var cardEl = createCard('pending', 'perm-' + requestId, toolName, argsHtml, true);

    var actionsEl = document.createElement('div');
    actionsEl.className = 'perm-actions';
    actionsEl.id = 'perm-actions-' + requestId;

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
    cardEl.after(actionsEl);

    pendingPermCards[requestId] = { cardEl: cardEl, toolName: toolName, args: args };
  }

  function addToolStart(id, name, args) {
    // Check if there's a recently approved permission card for this tool
    if (approvedPermMap[name]) {
      var info = approvedPermMap[name];
      var cardEl = info.cardEl;
      cardEl.id = 'tool-' + id;
      inputCards[id] = cardEl;
      delete approvedPermMap[name];
      return;
    }

    var argsHtml = formatArgs(args)
      ? '<div class="card-section-args"><div class="card-section-label">Input</div>' + escapeHtml(formatArgs(args)) + '</div>'
      : '';
    var el = createCard('running', 'tool-' + id, name, argsHtml, false);
    inputCards[id] = el;
  }

  function addToolResult(id, name, result) {
    var cardEl = inputCards[id];
    if (!cardEl) {
      var displayName = name || 'output';
      var truncated = result.length > 500 ? result.slice(0, 500) : result;
      var isLong = result.length > 500;
      var bodyHtml = '<div class="card-section-result"><div class="card-section-label">Output</div>' +
        escapeHtml(truncated) +
        (isLong ? '<button class="expand-output-btn">Show full output (' + result.length + ' chars)</button>' +
          '<div class="full-output">' + escapeHtml(result) + '</div>' : '') +
        '</div>';
      createCard('complete', 'result-' + id, displayName, bodyHtml, false);
      return;
    }

    var indicator = cardEl.querySelector('.card-indicator');
    if (indicator) indicator.style.animation = 'none';

    cardEl.dataset.state = 'complete';

    var truncated = result.length > 500 ? result.slice(0, 500) : result;
    var isLong = result.length > 500;
    var body = cardEl.querySelector('.card-body');
    if (body) {
      body.insertAdjacentHTML('beforeend',
        '<div class="card-section-result"><div class="card-section-label">Output</div>' +
        escapeHtml(truncated) +
        (isLong ? '<button class="expand-output-btn">Show full output (' + result.length + ' chars)</button>' +
          '<div class="full-output">' + escapeHtml(result) + '</div>' : '') +
        '</div>'
      );
    }

    cardEl.classList.remove('expanded');
    MoCode.Messages.scrollToBottom();
  }

  function addBadge(el, text, cls) {
    var header = el.querySelector('.card-header');
    var existing = header.querySelector('.card-badge');
    if (existing) existing.remove();
    var badge = document.createElement('span');
    badge.className = 'card-badge ' + cls;
    badge.textContent = text;
    header.insertBefore(badge, header.querySelector('.card-toggle'));
  }

  function resolvePermissionCard(requestId, approved) {
    var info = pendingPermCards[requestId];
    if (!info) return;

    var cardEl = info.cardEl;
    var actions = document.getElementById('perm-actions-' + requestId);
    if (actions) actions.remove();
    var desc = cardEl.querySelector('.perm-desc');
    if (desc) desc.remove();

    if (approved) {
      cardEl.dataset.state = 'running';
      addBadge(cardEl, 'Approved', 'badge-approved');
      cardEl.classList.remove('expanded');
      // Store by toolName so the next tool_start can find it
      approvedPermMap[info.toolName] = { cardEl: cardEl, requestId: requestId };
    } else {
      cardEl.dataset.state = 'denied';
      addBadge(cardEl, 'Denied', 'badge-denied');
      deniedToolNames[info.toolName] = true;
    }

    delete pendingPermCards[requestId];
  }

  async function resolvePermission(requestId, response) {
    var actions = document.getElementById('perm-actions-' + requestId);
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

  function isDenied(toolName) {
    return !!deniedToolNames[toolName];
  }

  function clearDenied(toolName) {
    delete deniedToolNames[toolName];
  }

  function clear() {
    pendingPermCards = {};
    inputCards = {};
    approvedPermMap = {};
    deniedToolNames = {};
  }

  return {
    init: init,
    createCard: createCard,
    createFromHistory: createFromHistory,
    addPermission: addPermission,
    addToolStart: addToolStart,
    addToolResult: addToolResult,
    resolvePermissionCard: resolvePermissionCard,
    resolvePermission: resolvePermission,
    isDenied: isDenied,
    clearDenied: clearDenied,
    clear: clear,
  };
})();
