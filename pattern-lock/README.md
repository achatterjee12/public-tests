# pattern-lock

Android-style 3×3 swipe-pattern gate for single-user web apps. No dependencies, one ES module, touch and mouse via pointer events.

```html
<template id="app"> ...your app... </template>
<script type="module">
  import { protect } from "./pattern-lock.js";
  const gate = protect({ app: document.getElementById("app") });
  // gate.changePattern()  -> Promise<boolean>, only after unlock
  // gate.lock()           -> forget the session and reload
</script>
```

Dots are numbered `0..8` (row by row). The built-in starting pattern is `[0, 1, 2, 5, 8]`; override with `defaultPattern`.
Once the user changes it, a salted PBKDF2 hash is stored in `localStorage` and replaces the default.

Behaviour: swiping across a dot selects it (0→2 also selects 1), at least 4 dots, 5 wrong tries → 30 s lockout,
unlocked state lasts for the tab session (`remember: "none"` to always ask). Needs https or localhost (WebCrypto).

Options: `app`, `mount`, `title`, `defaultPattern`, `minLength`, `storageKey`, `remember`, `maxAttempts`, `lockoutSeconds`, `onUnlock`.

**Limits:** this is client-side only. It keeps casual visitors out, but anyone who can read the page source or storage
can bypass it. Keep real secrets behind a server.

Tests: `node pattern-lock/test.mjs`. Demo: `pattern-lock/index.html` (serve over http, e.g. `python3 -m http.server`).
