document.addEventListener('DOMContentLoaded', function () {
  // Setup Markdown
  MoCode.Messages.setupMarkdown();

  // Init modules
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

  // Sidebar toggle
  var sidebarToggle = document.getElementById('sidebar-toggle');
  var sidebar = document.getElementById('sidebar');
  if (sidebarToggle && sidebar) {
    sidebarToggle.addEventListener('click', function () {
      sidebar.classList.toggle('collapsed');
    });
  }

  // New chat button
  var newChatBtn = document.getElementById('new-chat-btn');
  if (newChatBtn) {
    newChatBtn.addEventListener('click', function () {
      MoCode.Chat.newChat();
    });
  }

  // Initial load
  MoCode.Sidebar.load();
  MoCode.Chat.fetchStatus();
  MoCode.Messages.updateEmptyState(emptyState);

  // Focus input
  var inputEl = document.getElementById('input');
  if (inputEl) inputEl.focus();
});
