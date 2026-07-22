/**
 * Mounts the chat widget on every page, for logged-in visitors only.
 * Copy to: plugins/onbo.client.js  (the .client suffix keeps it out of SSR —
 * the widget touches document/window and must never run on the server).
 *
 * The widget file itself is loaded from /onbo-widget.js, i.e. from public/, so
 * it stays outside the bundle and can be updated by replacing one file.
 */
export default defineNuxtPlugin(() => {
  let widget = null

  const mount = async () => {
    if (widget) return
    // The specifier is a variable on purpose: that, plus @vite-ignore, stops the
    // bundler from trying to resolve a public/ file at build time.
    const url = '/onbo-widget.js'
    const { init } = await import(/* @vite-ignore */ url)
    widget = init({
      endpoint: '/api/assistant/chat',
      // /confirm and /welcome are derived as siblings of endpoint automatically.
      voiceEndpoint: '/api/assistant/voice',   // drop this line if you skipped voice.post.js
      title: 'Помощник',
      locale: 'ru',
    })
  }

  const unmount = () => {
    widget?.destroy()
    widget = null
  }

  // Replace with your own "is the visitor logged in" signal. Anything reactive
  // works — a Pinia store, useState, a composable.
  const auth = useAuthStore()
  watch(
    () => auth.isAuthenticated,
    (yes) => (yes ? mount() : unmount()),
    { immediate: true },
  )
})
