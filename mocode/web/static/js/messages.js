var MoCode = MoCode || {};

MoCode.Messages = (function () {
  var containerEl;

  function init(el) {
    containerEl = el;
  }

  function setupMarkdown() {
    if (typeof marked === 'undefined') return;

    var renderer = new marked.Renderer();

    renderer.code = function (code, lang) {
      var language = lang || '';
      var highlighted;
      if (language && typeof hljs !== 'undefined' && hljs.getLanguage(language)) {
        try {
          highlighted = hljs.highlight(code, { language: language }).value;
        } catch (_) {
          highlighted = escapeHtml(code);
        }
      } else if (typeof hljs !== 'undefined') {
        try {
          highlighted = hljs.highlightAuto(code).value;
        } catch (_) {
          highlighted = escapeHtml(code);
        }
      } else {
        highlighted = escapeHtml(code);
      }

      var langLabel = language ? '<span class="code-lang">' + escapeHtml(language) + '</span>' : '';
      return '<div class="code-block-wrapper">' +
        '<div class="code-block-header">' +
          langLabel +
          '<button class="code-copy-btn" title="Copy code">Copy</button>' +
        '</div>' +
        '<pre><code class="hljs' + (language ? ' language-' + language : '') + '">' + highlighted + '</code></pre>' +
      '</div>';
    };

    marked.setOptions({
      breaks: true,
      gfm: true,
      renderer: renderer,
    });
  }

  function renderMarkdown(text) {
    if (!text) return '';
    if (typeof marked !== 'undefined') {
      return marked.parse(text);
    }
    return simpleFormat(text);
  }

  function simpleFormat(text) {
    if (!text) return '';
    var html = escapeHtml(text);
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, function (_, lang, code) {
      return '<pre><code>' + code.trimEnd() + '</code></pre>';
    });
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/__(.+?)__/g, '<strong>$1</strong>');
    html = html.replace(/\n\n+/g, '</p><p>');
    html = html.replace(/\n/g, '<br>');
    if (!html.startsWith('<')) html = '<p>' + html + '</p>';
    return html;
  }

  function escapeHtml(s) {
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  function createUser(text) {
    var el = document.createElement('div');
    el.className = 'msg msg-user';
    el.innerHTML = '<div class="msg-bubble">' + escapeHtml(text) + '</div>';
    containerEl.appendChild(el);
    scrollToBottom();
    return el;
  }

  function createAssistant(content) {
    removeTypingIndicator();
    var el = document.createElement('div');
    el.className = 'msg msg-assistant';
    el.innerHTML = '<div class="msg-bubble">' + renderMarkdown(content) + '</div>';
    containerEl.appendChild(el);
    scrollToBottom();
    return el;
  }

  function createError(message) {
    removeTypingIndicator();
    var el = document.createElement('div');
    el.className = 'msg msg-error';
    el.innerHTML = '<div class="msg-bubble">' + escapeHtml(message) + '</div>';
    containerEl.appendChild(el);
    scrollToBottom();
  }

  function createInterrupted() {
    removeTypingIndicator();
    var el = document.createElement('div');
    el.className = 'msg msg-interrupted';
    el.innerHTML = '<div class="msg-bubble">Interrupted</div>';
    containerEl.appendChild(el);
    scrollToBottom();
  }

  function showTypingIndicator() {
    removeTypingIndicator();
    var el = document.createElement('div');
    el.className = 'typing-indicator';
    el.id = 'typing-indicator';
    el.innerHTML = '<span class="dot"></span><span class="dot"></span><span class="dot"></span>';
    containerEl.appendChild(el);
    scrollToBottom();
  }

  function removeTypingIndicator() {
    var el = document.getElementById('typing-indicator');
    if (el) el.remove();
  }

  function scrollToBottom(force) {
    if (!containerEl) return;
    var nearBottom = containerEl.scrollHeight - containerEl.scrollTop - containerEl.clientHeight < 200;
    if (force || nearBottom) {
      containerEl.scrollTo({
        top: containerEl.scrollHeight,
        behavior: nearBottom ? 'smooth' : 'auto',
      });
    }
  }

  function clear() {
    if (containerEl) containerEl.innerHTML = '';
  }

  function updateEmptyState(emptyEl) {
    var hasMessages = containerEl && containerEl.children.length > 0;
    if (hasMessages) {
      emptyEl.classList.remove('visible');
      containerEl.style.display = '';
    } else {
      emptyEl.classList.add('visible');
      containerEl.style.display = 'none';
    }
  }

  function extractText(content) {
    if (typeof content === 'string') return content;
    if (Array.isArray(content)) {
      return content.map(function (c) {
        if (typeof c === 'string') return c;
        if (c.type === 'text') return c.text || '';
        return JSON.stringify(c);
      }).join('\n');
    }
    return JSON.stringify(content);
  }

  function renderHistory(messages) {
    var i = 0;
    while (i < messages.length) {
      var msg = messages[i];
      if (msg.role === 'user') {
        createUser(extractText(msg.content));
        i++;
      } else if (msg.role === 'assistant') {
        if (msg.content) {
          createAssistant(extractText(msg.content));
        }
        // Correlate tool_calls with subsequent tool result messages
        if (msg.tool_calls && msg.tool_calls.length > 0) {
          var callInfo = {};
          var callOrder = [];
          for (var j = 0; j < msg.tool_calls.length; j++) {
            var tc = msg.tool_calls[j];
            var tcName = tc.function && tc.function.name || 'tool';
            var tcArgs = null;
            try { tcArgs = JSON.parse(tc.function.arguments); } catch (_) {}
            callInfo[tc.id] = { name: tcName, args: tcArgs };
            callOrder.push(tc.id);
          }
          // Collect following tool result messages
          var toolResults = {};
          var k = i + 1;
          while (k < messages.length && messages[k].role === 'tool') {
            toolResults[messages[k].tool_call_id] = extractText(messages[k].content);
            k++;
          }
          // Create cards with both input and output
          for (var m = 0; m < callOrder.length; m++) {
            var info = callInfo[callOrder[m]];
            var result = toolResults[callOrder[m]] || '';
            var truncated = result.length > 2000 ? result.slice(0, 2000) + '\n...(truncated)' : result;
            MoCode.ToolCards.createFromHistory(info.name, truncated, info.args);
          }
          i = k;
        } else {
          i++;
        }
      } else if (msg.role === 'tool') {
        // Orphan tool result (fallback)
        MoCode.ToolCards.createFromHistory(msg.name || 'tool', extractText(msg.content));
        i++;
      } else {
        i++;
      }
    }
    scrollToBottom(true);
  }

  // Code copy button handler - event delegation
  document.addEventListener('click', function (e) {
    if (e.target.classList.contains('code-copy-btn')) {
      var wrapper = e.target.closest('.code-block-wrapper');
      if (!wrapper) return;
      var codeEl = wrapper.querySelector('code');
      if (!codeEl) return;
      navigator.clipboard.writeText(codeEl.textContent).then(function () {
        e.target.textContent = 'Copied!';
        setTimeout(function () { e.target.textContent = 'Copy'; }, 1500);
      });
    }
  });

  return {
    init: init,
    setupMarkdown: setupMarkdown,
    createUser: createUser,
    createAssistant: createAssistant,
    createError: createError,
    createInterrupted: createInterrupted,
    showTypingIndicator: showTypingIndicator,
    removeTypingIndicator: removeTypingIndicator,
    scrollToBottom: scrollToBottom,
    clear: clear,
    updateEmptyState: updateEmptyState,
    extractText: extractText,
    renderHistory: renderHistory,
    escapeHtml: escapeHtml,
  };
})();
