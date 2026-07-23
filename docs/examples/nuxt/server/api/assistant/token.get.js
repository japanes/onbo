/**
 * GET /api/assistant/token — the entire server side of embedding onbo.
 *
 * The widget in the browser talks to onbo directly. The only thing it cannot do
 * on its own is prove who the visitor is: a browser-supplied user id is just a
 * string anyone can retype, and whatever tells onbo who is asking also decides
 * what that person is allowed to see. So this route — which does know the
 * visitor, from the session cookie — hands out a short-lived signed token.
 *
 * The secret never leaves the server. Without it a token cannot be produced,
 * and onbo rejects any token whose signature does not check out, so nobody can
 * promote themselves by editing the payload.
 *
 * Nothing here is onbo-specific machinery you have to maintain: it is one HMAC
 * over three claims, using node:crypto, with no dependency to install.
 */
import { createHmac } from 'node:crypto'

// Short on purpose: the widget re-fetches shortly before expiry, so a token
// that leaks is useful for minutes, not forever.
const TTL_SECONDS = 300

function b64url(value) {
  return Buffer.from(value).toString('base64url')
}

function signToken(claims, secret) {
  const header = b64url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
  const payload = b64url(JSON.stringify(claims))
  const signature = createHmac('sha256', secret)
    .update(`${header}.${payload}`)
    .digest('base64url')
  return `${header}.${payload}.${signature}`
}

export default defineEventHandler(async (event) => {
  const secret = process.env.ONBO_JWT_SECRET
  if (!secret) {
    // Fail loudly rather than serving unsigned nonsense that onbo will refuse.
    throw createError({ statusCode: 500, message: 'ONBO_JWT_SECRET is not set' })
  }

  // ---------------------------------------------------------------------
  // THE ONE THING TO ADAPT: identify the logged-in visitor.
  // Replace this with however your app resolves the current user; throw 401
  // when there is nobody logged in, and the widget stays anonymous.
  // ---------------------------------------------------------------------
  const { requireAuth } = await import('../../utils/auth.js')
  const user = await requireAuth(event)

  const claims = {
    sub: String(user.id),
    exp: Math.floor(Date.now() / 1000) + TTL_SECONDS,

    // What the person may see. onbo matches these against the tags on
    // knowledge-base entries and actions — omit them and everyone gets only
    // what is published to everyone.
    roles: user.role?.name ? [user.role.name] : [],
    department: user.department || undefined,

    // The person's own API credential, so actions ("create the project
    // watermelon") run against your product AS THEM and your usual permission
    // checks still apply. Drop this line and onbo falls back to the single
    // service key in its own settings — simpler, but then every action runs
    // with one identity and your per-user rules no longer constrain it.
    //
    // Trade-off worth knowing: with this claim the browser holds a token that
    // works against your API directly. It is short-lived, and the visitor could
    // already call that API as themselves — but it does bypass any rules that
    // live only in your HTTP layer. Omit it if that matters more than per-user
    // actions do.
    product_token: event.context.token,
  }

  return { token: signToken(claims, secret) }
})
