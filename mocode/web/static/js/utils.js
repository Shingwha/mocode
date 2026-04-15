var MoCode = MoCode || {};

MoCode.Utils = (function () {
  function escapeHtml(s) {
    if (s === undefined || s === null) return '';
    s = String(s);
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  function logError(context, err) {
    if (typeof console !== 'undefined' && console.error) {
      console.error('[MoCode] ' + context, err);
    }
  }

  return {
    escapeHtml: escapeHtml,
    logError: logError,
  };
})();
