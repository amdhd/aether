// Apply the persisted (or default dark) theme before paint to avoid a flash.
//
// This lives in its own file rather than inline in index.html so the CSP can say
// `script-src 'self'` with no 'unsafe-inline' and no hash. A hash would work too,
// but it silently stops matching the moment anyone edits the script, and the
// symptom — a theme flash on load — is subtle enough to survive review.
;(function () {
  try {
    var stored = localStorage.getItem('aether-theme')
    // Dark (Dusk's slate) unless the user has picked light.
    var dark = stored !== 'light'
    if (dark) document.documentElement.classList.add('dark')
  } catch (e) {}
})()
