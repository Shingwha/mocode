var MoCode = MoCode || {};

MoCode.Settings = (function () {
  var isOpen = false;
  var configCache = null;
  var cards = {};
  var toastTimer = null;

  // DOM elements
  var overlayEl, modalEl, cardsContainerEl, toastEl;

  function init() {
    overlayEl = document.getElementById('settings-overlay');
    modalEl = document.getElementById('settings-modal');
    cardsContainerEl = document.getElementById('settings-cards');
    toastEl = document.getElementById('toast');

    if (!overlayEl || !modalEl || !cardsContainerEl || !toastEl) {
      console.error('Settings: Missing required DOM elements');
      return;
    }

    // Close handlers
    overlayEl.addEventListener('click', close);
    document.getElementById('settings-close').addEventListener('click', close);
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape' && isOpen) close();
    });

    // Event delegation for cards
    modalEl.addEventListener('click', handleClick);
  }

  async function open() {
    await loadConfig();
    render();
    show();
  }

  function close() {
    hide();
  }

  async function loadConfig() {
    configCache = await MoCode.Api.getConfig();
  }

  function registerCard(name, renderer) {
    cards[name] = renderer;
  }

  function render() {
    cardsContainerEl.innerHTML = '';
    Object.keys(cards).forEach(function(name) {
      var cardEl = cards[name].render(configCache);
      cardsContainerEl.appendChild(cardEl);
    });
  }

  function show() {
    isOpen = true;
    overlayEl.classList.add('visible');
    modalEl.classList.add('visible');
    document.body.style.overflow = 'hidden';
  }

  function hide() {
    isOpen = false;
    overlayEl.classList.remove('visible');
    modalEl.classList.remove('visible');
    document.body.style.overflow = '';
  }

  function showToast(message, type) {
    if (toastTimer) clearTimeout(toastTimer);
    toastEl.textContent = message;
    toastEl.className = 'toast visible';
    if (type) toastEl.classList.add(type);
    toastTimer = setTimeout(function() {
      toastEl.classList.remove('visible');
    }, 2000);
  }

  function showError(inputEl, message) {
    var errorEl = inputEl.parentNode.querySelector('.form-error');
    if (!errorEl) {
      errorEl = document.createElement('div');
      errorEl.className = 'form-error';
      inputEl.parentNode.appendChild(errorEl);
    }
    errorEl.textContent = message;
    // Auto-clear on input
    var clearHandler = function() {
      errorEl.textContent = '';
      inputEl.removeEventListener('input', clearHandler);
    };
    inputEl.addEventListener('input', clearHandler);
  }

  function handleClick(e) {
    var actionEl = e.target.closest('[data-action]');
    if (!actionEl) return;
    var action = actionEl.dataset.action;
    var key = actionEl.dataset.key;
    var value = actionEl.dataset.value;

    // Find card that handles this action
    Object.keys(cards).forEach(function(name) {
      var card = cards[name];
      if (card.onAction && card.onAction(action, key, value, e)) {
        e.preventDefault();
        e.stopPropagation();
      }
    });
  }

  // ========== Card 1: Model Selection ==========
  var modelCard = {
    render: function(config) {
      var div = document.createElement('div');
      div.className = 'settings-card expanded';
      div.dataset.card = 'model';
      div.innerHTML = '<div class="card-header" tabindex="0"><span class="card-title">Model Selection</span><svg class="card-toggle" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></div>' +
        '<div class="card-body">' +
          '<div class="form-row">' +
            '<div class="form-group">' +
              '<label for="settings-provider">Provider</label>' +
              '<select id="settings-provider">' + this.renderProviderOptions(config.providers, config.current_provider) + '</select>' +
            '</div>' +
            '<div class="form-group">' +
              '<label for="settings-model">Model</label>' +
              '<select id="settings-model">' + this.renderModelOptions(config.providers, config.current_provider, config.current_model) + '</select>' +
            '</div>' +
          '</div>' +
        '</div>';
      // Bind events after render
      setTimeout(function() {
        var select = div.querySelector('.card-header');
        if (select) {
          select.addEventListener('click', function() {
            div.classList.toggle('expanded');
          });
        }
        var providerSelect = div.querySelector('#settings-provider');
        var modelSelect = div.querySelector('#settings-model');
        if (providerSelect) {
          providerSelect.addEventListener('change', function() {
            modelCard.onProviderChange(providerSelect.value, modelSelect);
          });
        }
        if (modelSelect) {
          modelSelect.addEventListener('change', function() {
            modelCard.onModelChange(modelSelect.value);
          });
        }
      }, 0);
      return div;
    },

    renderProviderOptions: function(providers, currentProvider) {
      var html = '';
      Object.keys(providers).forEach(function(key) {
        var p = providers[key];
        html += '<option value="' + escapeHtml(key) + '"' + (key === currentProvider ? ' selected' : '') + '>' + escapeHtml(p.name) + '</option>';
      });
      return html;
    },

    renderModelOptions: function(providers, currentProvider, currentModel) {
      var html = '';
      var models = providers[currentProvider] ? providers[currentProvider].models : [];
      models.forEach(function(m) {
        html += '<option value="' + escapeHtml(m) + '"' + (m === currentModel ? ' selected' : '') + '>' + escapeHtml(m) + '</option>';
      });
      return html;
    },

    onProviderChange: async function(providerKey, modelSelect) {
      var result = await MoCode.Api.switchProvider({provider: providerKey});
      if (result) {
        // Reload config to get updated providers/models
        var config = await MoCode.Api.getConfig();
        modelSelect.innerHTML = modelCard.renderModelOptions(config.providers, config.current_provider, config.current_model);
        MoCode.Settings.configCache = config;
        MoCode.Settings.showToast('Provider switched');
      } else {
        MoCode.Settings.showToast('Failed to switch provider', 'error');
      }
    },

    onModelChange: async function(model) {
      var provider = document.getElementById('settings-provider').value;
      var result = await MoCode.Api.switchModel({provider: provider, model: model});
      if (result) {
        MoCode.Settings.configCache = result;
        MoCode.Settings.showToast('Model switched');
      } else {
        MoCode.Settings.showToast('Failed to switch model', 'error');
      }
    },

    onAction: function(action, key, value, e) {
      // No direct actions for this card
      return false;
    },
  };

  // ========== Card 2: Provider Management ==========
  var providerCard = {
    render: function(config) {
      var self = this;
      var div = document.createElement('div');
      div.className = 'settings-card expanded';
      div.dataset.card = 'provider';
      div.innerHTML = '<div class="card-header" tabindex="0"><span class="card-title">Providers</span><svg class="card-toggle" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></div>' +
        '<div class="card-body">' +
          '<div id="provider-list" class="provider-list">' + this.renderProviderList(config.providers) + '</div>' +
          '<div style="margin-top: 12px;">' +
            '<button class="btn btn-secondary" data-action="show-add-form">+ Add Provider</button>' +
          '</div>' +
          '<div id="provider-form-container" style="display: none; margin-top: 12px;">' +
            this.renderFormHtml() +
          '</div>' +
        '</div>';

      setTimeout(function() {
        var header = div.querySelector('.card-header');
        if (header) {
          header.addEventListener('click', function() {
            div.classList.toggle('expanded');
          });
        }
        // Bind delete buttons
        div.querySelectorAll('[data-action="delete-provider"]').forEach(function(btn) {
          btn.addEventListener('click', function() {
            var key = btn.dataset.key;
            self.deleteProvider(key);
          });
        });
        // Bind edit buttons
        div.querySelectorAll('[data-action="edit-provider"]').forEach(function(btn) {
          btn.addEventListener('click', function() {
            var key = btn.dataset.key;
            self.showEditForm(key);
          });
        });
      }, 0);
      return div;
    },

    renderProviderList: function(providers) {
      if (!providers || Object.keys(providers).length === 0) {
        return '<div class="empty-providers">No providers configured</div>';
      }
      var html = '';
      Object.keys(providers).forEach(function(key) {
        var p = providers[key];
        var domain = p.base_url ? (new URL(p.base_url, 'http://placeholder')).hostname : p.base_url;
        html += '<div class="provider-item" data-key="' + escapeHtml(key) + '">' +
          '<div class="provider-info">' +
            '<div class="provider-name">' + escapeHtml(p.name) + '</div>' +
            '<div class="provider-key">' + escapeHtml(key) + '</div>' +
            '<div class="provider-url" title="' + escapeHtml(p.base_url) + '">' + escapeHtml(domain) + '</div>' +
            '<div class="provider-models">Models: ' + (p.models ? p.models.join(', ') : 'None') + '</div>' +
          '</div>' +
          '<div class="provider-actions">' +
            '<div class="provider-meta"><span class="key-status' + (p.api_key_set ? '' : ' missing') + '"></span>' + (p.api_key_set ? 'Set' : 'Not set') + '</div>' +
            '<button class="btn-icon btn-ghost" data-action="edit-provider" data-key="' + escapeHtml(key) + '" title="Edit"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>' +
            '<button class="btn-icon btn-ghost" data-action="delete-provider" data-key="' + escapeHtml(key) + '" title="Delete"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>' +
          '</div>' +
        '</div>';
      });
      return html;
    },

    renderFormHtml: function() {
      return '' +
        '<div class="modal-form">' +
          '<div class="form-row">' +
            '<div class="form-group">' +
              '<label for="provider-key">Key (ID)</label>' +
              '<input type="text" id="provider-key" placeholder="e.g., openai" autocomplete="off">' +
            '</div>' +
            '<div class="form-group">' +
              '<label for="provider-name">Display Name</label>' +
              '<input type="text" id="provider-name" placeholder="e.g., OpenAI" autocomplete="off">' +
            '</div>' +
          '</div>' +
          '<div class="form-group">' +
            '<label for="provider-url">Base URL</label>' +
            '<input type="text" id="provider-url" placeholder="https://api.openai.com/v1" autocomplete="off">' +
          '</div>' +
          '<div class="form-group">' +
            '<label for="provider-key-input">API Key</label>' +
            '<div class="input-with-button" style="display: flex; gap: 6px;">' +
              '<input type="password" id="provider-key-input" placeholder="sk-..." autocomplete="off" style="flex: 1;">' +
              '<button type="button" class="btn btn-secondary" id="toggle-key-visibility" style="flex-shrink: 0; padding: 7px 12px;">Show</button>' +
            '</div>' +
          '</div>' +
          '<div class="form-group">' +
            '<label for="provider-models">Models (one per line)</label>' +
            '<textarea id="provider-models" placeholder="gpt-4o&#10;gpt-4o-mini"></textarea>' +
          '</div>' +
          '<div class="form-actions">' +
            '<button class="btn btn-ghost" data-action="cancel-provider-form">Cancel</button>' +
            '<button class="btn btn-primary" data-action="save-provider">Save</button>' +
          '</div>' +
        '</div>';
    },

    showAddForm: function() {
      var container = document.getElementById('provider-form-container');
      container.style.display = 'block';
      container.innerHTML = this.renderFormHtml();
      var form = container.querySelector('.modal-form');
      form.dataset.mode = 'add';
      this.bindFormEvents(form);
    },

    showEditForm: function(key) {
      var self = this;
      var provider = configCache.providers[key];
      if (!provider) return;

      var container = document.getElementById('provider-form-container');
      container.style.display = 'block';
      container.innerHTML = this.renderFormHtml();
      var form = container.querySelector('.modal-form');
      form.dataset.mode = 'edit';
      form.dataset.editKey = key;

      // Populate fields
      form.querySelector('#provider-key').value = key;
      form.querySelector('#provider-name').value = provider.name || '';
      form.querySelector('#provider-url').value = provider.base_url || '';
      form.querySelector('#provider-key-input').value = provider.api_key || '';
      form.querySelector('#provider-models').value = (provider.models || []).join('\n');

      this.bindFormEvents(form);
    },

    bindFormEvents: function(form) {
      var self = this;
      var mode = form.dataset.mode;
      var editKey = form.dataset.editKey;

      // API key visibility toggle
      var toggleBtn = form.querySelector('#toggle-key-visibility');
      var apiKeyInput = form.querySelector('#provider-key-input');
      if (toggleBtn && apiKeyInput) {
        toggleBtn.addEventListener('click', function() {
          if (apiKeyInput.type === 'password') {
            apiKeyInput.type = 'text';
            toggleBtn.textContent = 'Hide';
          } else {
            apiKeyInput.type = 'password';
            toggleBtn.textContent = 'Show';
          }
        });
      }

      form.querySelector('[data-action="cancel-provider-form"]').addEventListener('click', function() {
        document.getElementById('provider-form-container').style.display = 'none';
      });

      form.querySelector('[data-action="save-provider"]').addEventListener('click', async function() {
        var keyInput = form.querySelector('#provider-key');
        var nameInput = form.querySelector('#provider-name');
        var urlInput = form.querySelector('#provider-url');
        var apiKeyInput = form.querySelector('#provider-key-input');
        var modelsInput = form.querySelector('#provider-models');

        // Validation
        var key = keyInput.value.trim();
        var name = nameInput.value.trim();
        var baseUrl = urlInput.value.trim();
        var apiKey = apiKeyInput.value.trim();
        var modelsText = modelsInput.value.trim();

        if (!key) {
          self.showError(keyInput, 'Key is required');
          return;
        }
        if (!/^[a-z0-9_]+$/.test(key)) {
          self.showError(keyInput, 'Key must be lowercase letters, numbers, or underscores');
          return;
        }
        if (!baseUrl) {
          self.showError(urlInput, 'Base URL is required');
          return;
        }
        try {
          new URL(baseUrl);
        } catch (_) {
          self.showError(urlInput, 'Invalid URL');
          return;
        }
        var models = modelsText ? modelsText.split('\n').map(function(m) { return m.trim(); }).filter(function(m) { return m; }) : [];

        var result;
        if (mode === 'add') {
          result = await MoCode.Api.addProvider({key: key, name: name, base_url: baseUrl, api_key: apiKey, models: models});
        } else {
          result = await MoCode.Api.updateProvider(editKey, {name: name, base_url: baseUrl, api_key: apiKey});
        }

        if (result) {
          document.getElementById('provider-form-container').style.display = 'none';
          MoCode.Settings.configCache = result;
          MoCode.Settings.render();
          MoCode.Settings.showToast(mode === 'add' ? 'Provider added' : 'Provider updated');
        } else {
          MoCode.Settings.showToast('Failed to ' + (mode === 'add' ? 'add' : 'update') + ' provider', 'error');
        }
      });
    },

    deleteProvider: async function(key) {
      var self = this;
      if (!confirm('Delete provider "' + key + '"? This cannot be undone.')) return;
      var result = await MoCode.Api.removeProvider(key);
      if (result) {
        MoCode.Settings.configCache = result;
        MoCode.Settings.render();
        MoCode.Settings.showToast('Provider deleted');
      } else {
        MoCode.Settings.showToast('Failed to delete provider', 'error');
      }
    },

    onAction: function(action, key, value, e) {
      if (action === 'show-add-form') {
        this.showAddForm();
        e.preventDefault();
        return true;
      }
      if (action === 'cancel-provider-form') {
        document.getElementById('provider-form-container').style.display = 'none';
        e.preventDefault();
        return true;
      }
      return false;
    },
  };

  // ========== Card 3: Mode Switch ==========
  var modeCard = {
    render: function(config) {
      var div = document.createElement('div');
      div.className = 'settings-card expanded';
      div.dataset.card = 'mode';
      div.innerHTML = '<div class="card-header" tabindex="0"><span class="card-title">Mode</span><svg class="card-toggle" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></div>' +
        '<div class="card-body">' +
          '<div class="form-group">' +
            '<label>Execution Mode</label>' +
            '<div class="radio-group">' +
              '<div>' +
                '<input type="radio" id="mode-normal" name="mode" value="normal"' + (config.mode === 'normal' ? ' checked' : '') + '>' +
                '<label class="radio-option" for="mode-normal">Normal</label>' +
              '</div>' +
              '<div>' +
                '<input type="radio" id="mode-yolo" name="mode" value="yolo"' + (config.mode === 'yolo' ? ' checked' : '') + '>' +
                '<label class="radio-option" for="mode-yolo">YOLO</label>' +
              '</div>' +
            '</div>' +
            '<div class="radio-desc">Normal: All tools require approval before execution. YOLO: Safe tools auto-approved, only dangerous operations require approval.</div>' +
          '</div>' +
        '</div>';

      setTimeout(function() {
        var header = div.querySelector('.card-header');
        if (header) {
          header.addEventListener('click', function() {
            div.classList.toggle('expanded');
          });
        }
        var radios = div.querySelectorAll('input[name="mode"]');
        radios.forEach(function(radio) {
          radio.addEventListener('change', function() {
            modeCard.onModeChange(radio.value);
          });
        });
      }, 0);
      return div;
    },

    onModeChange: async function(mode) {
      var result = await MoCode.Api.switchMode(mode);
      if (result) {
        MoCode.Settings.configCache = result;
        MoCode.Settings.showToast('Mode switched to ' + mode);
      } else {
        MoCode.Settings.showToast('Failed to switch mode', 'error');
      }
    },

    onAction: function(action, key, value, e) {
      return false;
    },
  };

  // Register cards (order matters)
  registerCard('model', modelCard);
  registerCard('provider', providerCard);
  registerCard('mode', modeCard);
  // Phase 2: registerCard('execution', executionCard);

  return {
    init: init,
    open: open,
    close: close,
    configCache: configCache,
    showToast: showToast,
    showError: showError,
    render: render,
    _internal: {
      getConfig: function() { return configCache; },
      reload: loadConfig,
    },
  };
})();

// Utility: HTML escape
function escapeHtml(s) {
  if (s === undefined || s === null) return '';
  s = String(s);
  var d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}
