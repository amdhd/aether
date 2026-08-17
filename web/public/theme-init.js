// Apply the persisted (or system) theme before paint to avoid a flash.
//
// This lives in its own file rather than inline in index.html so the CSP can say
// `script-src 'self'` with no 'unsafe-inline' and no hash. A hash would work too,
// but it silently stops matching the moment anyone edits the script, and the
// symptom — a theme flash on load — is subtle enough to survive review.
;(function () {
  try {
    var stored = localStorage.getItem('aether-theme')
    var dark = stored
      ? stored === 'dark'
      : window.matchMedia('(prefers-color-scheme: dark)').matches
    if (dark) document.documentElement.classList.add('dark')
  } catch (e) {}
})()
