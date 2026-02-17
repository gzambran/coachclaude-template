/* Coach Claude shared navigation — include in weekly pages via <script src="../nav.js"></script> */
(function () {
  var PIN = '0000';
  var STORAGE_KEY = 'coachclaude_pin';

  /* PIN gate — redirect to index if not authenticated */
  if (localStorage.getItem(STORAGE_KEY) !== PIN) {
    window.location.href = '../index.html';
    return;
  }

  /* Inject header bar */
  var nav = document.createElement('div');
  nav.id = 'cc-nav';

  var backLink = document.createElement('a');
  backLink.href = '../index.html';
  backLink.textContent = '\u2190 Coach Claude';
  backLink.id = 'cc-nav-back';

  var lockBtn = document.createElement('span');
  lockBtn.textContent = 'Lock';
  lockBtn.id = 'cc-nav-lock';
  lockBtn.addEventListener('click', function () {
    localStorage.removeItem(STORAGE_KEY);
    window.location.href = '../index.html';
  });

  nav.appendChild(backLink);
  nav.appendChild(lockBtn);
  document.body.insertBefore(nav, document.body.firstChild);

  /* Inject styles */
  var style = document.createElement('style');
  style.textContent =
    '#cc-nav { display: flex; justify-content: space-between; align-items: center; padding: 10px 0; margin-bottom: 16px; border-bottom: 1px solid #2a2a2a; }' +
    '#cc-nav-back { color: #888; text-decoration: none; font-size: 0.85rem; font-weight: 600; }' +
    '#cc-nav-back:hover { color: #fff; }' +
    '#cc-nav-lock { color: #555; font-size: 0.75rem; cursor: pointer; }' +
    '#cc-nav-lock:hover { color: #888; }';
  document.head.appendChild(style);
})();
