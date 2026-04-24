var MoCode = MoCode || {};

MoCode.Messages = (function () {
  var containerEl;

  function init(el) {
    containerEl = el;
  }

  var COPY_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  var CHECK_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';

  function setupMarkdown() {
    if (typeof marked === 'undefined') return;

    marked.use({
      useNewRenderer: true,
      renderer: {
        code: function (_ref) {
          var text = _ref.text;
          var lang = _ref.lang;
          var language = lang || '';
          var highlighted;
          if (language && typeof hljs !== 'undefined' && hljs.getLanguage(language)) {
            try {
              highlighted = hljs.highlight(text, { language: language }).value;
            } catch (e) {
              MoCode.Utils.logError('hljsHighlight', e);
              highlighted = escapeHtml(text);
            }
          } else if (typeof hljs !== 'undefined') {
            try {
              highlighted = hljs.highlightAuto(text).value;
            } catch (e) {
              MoCode.Utils.logError('hljsHighlightAuto', e);
              highlighted = escapeHtml(text);
            }
          } else {
            highlighted = escapeHtml(text);
          }

          var langLabel = language ? '<span class="code-lang">' + escapeHtml(language) + '</span>' : '';
          return '<div class="code-block-wrapper">' +
            '<div class="code-block-header">' +
              langLabel +
              '<button class="code-copy-btn" title="Copy code">' + COPY_SVG + '</button>' +
            '</div>' +
            '<pre><code class="hljs' + (language ? ' language-' + language : '') + '">' + highlighted + '</code></pre>' +
          '</div>';
        },
      },
      breaks: true,
      gfm: true,
    });
  }

  function renderMarkdown(text) {
    if (!text) return '';
    if (typeof marked !== 'undefined') {
      return marked.parse(text);
    }
    return '<p>' + escapeHtml(text).replace(/\n/g, '<br>') + '</p>';
  }

  function escapeHtml(s) {
    return MoCode.Utils.escapeHtml(s);
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
        if (msg.tool_calls && msg.tool_calls.length > 0) {
          var callInfo = {};
          var callOrder = [];
          for (var j = 0; j < msg.tool_calls.length; j++) {
            var tc = msg.tool_calls[j];
            var tcName = tc.function && tc.function.name || 'tool';
            var tcArgs = null;
            try { tcArgs = JSON.parse(tc.function.arguments); } catch (e) {
              MoCode.Utils.logError('parseToolArgs', e);
            }
            callInfo[tc.id] = { name: tcName, args: tcArgs };
            callOrder.push(tc.id);
          }
          var toolResults = {};
          var k = i + 1;
          while (k < messages.length && messages[k].role === 'tool') {
            toolResults[messages[k].tool_call_id] = extractText(messages[k].content);
            k++;
          }
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
        MoCode.ToolCards.createFromHistory(msg.name || 'tool', extractText(msg.content));
        i++;
      } else {
        i++;
      }
    }
    scrollToBottom(true);
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('.code-copy-btn');
    if (!btn) return;
    var wrapper = btn.closest('.code-block-wrapper');
    if (!wrapper) return;
    var codeEl = wrapper.querySelector('code');
    if (!codeEl) return;
    navigator.clipboard.writeText(codeEl.textContent).then(function () {
      btn.innerHTML = CHECK_SVG;
      btn.classList.add('copied');
      setTimeout(function () {
        btn.innerHTML = COPY_SVG;
        btn.classList.remove('copied');
      }, 1500);
    }).catch(function (e) {
      MoCode.Utils.logError('copyCode', e);
      btn.innerHTML = CHECK_SVG;
      btn.classList.add('copied');
      setTimeout(function () {
        btn.innerHTML = COPY_SVG;
        btn.classList.remove('copied');
      }, 1500);
    });
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
