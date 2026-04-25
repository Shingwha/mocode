var MoCode = MoCode || {};

MoCode.Theme = (function () {
  var STORAGE_KEY = 'mocode-theme';
  var OPTIONS = ['system', 'light', 'dark'];
  var _timer = null;

  function getStored() {
    try {
      var val = localStorage.getItem(STORAGE_KEY);
      return OPTIONS.indexOf(val) !== -1 ? val : null;
    } catch (e) {
      return null;
    }
  }

  function getSystemPreference() {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function getResolved() {
    var stored = getStored();
    if (!stored || stored === 'system') return getSystemPreference();
    return stored;
  }

  function apply(theme) {
    var resolved = theme === 'system' ? getSystemPreference() : theme;
    var html = document.documentElement;

    html.setAttribute('data-theme-transitioning', '');
    html.setAttribute('data-theme', resolved);

    clearTimeout(_timer);
    _timer = setTimeout(function () {
      html.removeAttribute('data-theme-transitioning');
    }, 300);

    var lightLink = document.getElementById('hljs-light');
    var darkLink = document.getElementById('hljs-dark');
    if (lightLink && darkLink) {
      lightLink.media = resolved === 'light' ? 'all' : 'not all';
      darkLink.media = resolved === 'dark' ? 'all' : 'not all';
    }
  }

  function set(theme) {
    apply(theme);
    try { localStorage.setItem(STORAGE_KEY, theme); } catch (e) {}
  }

  function init() {
    var stored = getStored();
    apply(stored || 'system');

    var mq = window.matchMedia('(prefers-color-scheme: dark)');
    if (mq.addEventListener) {
      mq.addEventListener('change', function () {
        if (!getStored() || getStored() === 'system') {
          apply('system');
        }
      });
    }
  }

  function getCurrent() {
    return getStored() || 'system';
  }

  return {
    init: init,
    set: set,
    getCurrent: getCurrent,
    getResolved: getResolved,
  };
})();
