/**
 * onbo upstream for a Nuxt 3 / Nitro app (mode A — proxy).
 *
 * The browser talks only to /api/assistant/* on your own origin; this file is
 * the only place that knows where onbo lives. The user id is attached
 * server-side from the session and is NEVER read from the request body — a
 * browser cannot be trusted with someone else's id.
 *
 * Copy to: server/utils/onbo.js
 *
 * Nitro auto-imports everything under server/utils, so the four route files
 * call onboPost() / assistantUser() without importing anything.
 */

/**
 * WHO IS ASKING. Replace the body with your own auth — this is the one function
 * you have to adapt. It must return a stable, unique id of the logged-in person
 * (the same string you registered with `onbo users add`) or throw 401.
 *
 * Typical shapes:
 *   const user = await requireAuth(event)          // your own helper
 *   const { user } = await getUserSession(event)   // nuxt-auth-utils
 *   const session = await getServerSession(event)  // @sidebase/nuxt-auth
 */
export async function assistantUser(event) {
  const user = event.context.user
  if (!user) throw createError({ statusCode: 401, message: 'Not authenticated' })
  return String(user.id)
}

/** Base address of onbo. Same docker network -> http://app:18000. */
export function onboBase() {
  const base =
    useRuntimeConfig().onboUrl || process.env.ONBO_URL || 'http://localhost:18000'
  return base.replace(/\/$/, '')
}

/**
 * POST JSON to onbo. Generous timeout on purpose: a cold model plus a knowledge
 * search is slower than an ordinary API call.
 */
export async function onboPost(path, payload, { timeout = 60000 } = {}) {
  try {
    return await $fetch(`${onboBase()}/${path}`, { method: 'POST', body: payload, timeout })
  } catch (err) {
    // Do not leak the upstream address or its error text to the browser.
    throw createError({
      statusCode: err.response?.status || 502,
      message: 'Assistant is unavailable',
    })
  }
}
