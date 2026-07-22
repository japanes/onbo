/**
 * POST /api/assistant/confirm — body: { action, approved }
 * Answers the Ok/Cancel card of an action with mode: confirm.
 * Copy to: server/api/assistant/confirm.post.js
 */
export default defineEventHandler(async (event) => {
  const userId = await assistantUser(event)
  const body = await readBody(event)

  const action = String(body?.action || '')
  if (!action) throw createError({ statusCode: 400, message: 'action is required' })

  return await onboPost('confirm', {
    user_id: userId,
    action,
    approved: body?.approved === true,
  })
})
