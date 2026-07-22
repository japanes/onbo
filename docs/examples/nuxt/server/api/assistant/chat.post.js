/**
 * POST /api/assistant/chat — body: { text, locale? }
 * Copy to: server/api/assistant/chat.post.js
 */
export default defineEventHandler(async (event) => {
  const userId = await assistantUser(event)
  const body = await readBody(event)

  const text = String(body?.text || '').slice(0, 4000)
  if (!text) throw createError({ statusCode: 400, message: 'text is required' })

  return await onboPost('chat', {
    user_id: userId,            // from the session, never from the body
    text,
    locale: body?.locale || 'ru',
  })
})
