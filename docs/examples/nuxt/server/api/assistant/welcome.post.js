/**
 * POST /api/assistant/welcome — no body.
 * First-contact digest, tailored to what this user is allowed to see.
 * Copy to: server/api/assistant/welcome.post.js
 */
export default defineEventHandler(async (event) => {
  const userId = await assistantUser(event)
  return await onboPost('welcome', { user_id: userId })
})
