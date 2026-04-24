document.addEventListener('DOMContentLoaded', function () {
  MoCode.Messages.setupMarkdown();

  var messagesEl = document.getElementById('messages');
  var sessionListEl = document.getElementById('session-list');
  var emptyState = document.getElementById('empty-state');

  MoCode.Messages.init(messagesEl);
  MoCode.ToolCards.init(messagesEl);
  MoCode.Sidebar.init(sessionListEl, {
    onSwitch: function (id) { MoCode.Chat.switchSession(id); },
    onDelete: function (id) { MoCode.Chat.deleteSession(id); },
  });
  MoCode.Chat.init();

  var sidebarToggle = document.getElementById('sidebar-toggle');
  var sidebar = document.getElementById('sidebar');
  var overlay = document.getElementById('sidebar-overlay');

  function updateOverlay() {
    if (!overlay) return;
    var isMobile = window.matchMedia('(max-width: 768px)').matches;
    var isSidebarOpen = !sidebar.classList.contains('collapsed');

    if (isMobile && isSidebarOpen) {
      overlay.classList.add('visible');
    } else {
      overlay.classList.remove('visible');
    }
  }

  function closeSidebar() {
    sidebar.classList.add('collapsed');
    updateOverlay();
  }

  if (sidebarToggle && sidebar) {
    sidebarToggle.addEventListener('click', function () {
      sidebar.classList.toggle('collapsed');
      updateOverlay();
    });
  }

  if (overlay) {
    overlay.addEventListener('click', closeSidebar);
  }

  var newChatBtn = document.getElementById('new-chat-btn');
  if (newChatBtn) {
    newChatBtn.addEventListener('click', function () {
      MoCode.Chat.newChat();
    });
  }

  MoCode.Sidebar.load();
  MoCode.Chat.fetchStatus();
  MoCode.Messages.updateEmptyState(emptyState);

  MoCode.Settings.init();
  MoCode.Sidebar.setupSettingsButton();

  window.addEventListener('resize', updateOverlay);
  updateOverlay();

  var inputEl = document.getElementById('input');
  if (inputEl) inputEl.focus();
});
