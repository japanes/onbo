/**
 * POST /api/assistant/voice — multipart: audio (blob) + locale.
 * Optional: skip this file and the widget simply has no microphone button.
 * Copy to: server/api/assistant/voice.post.js
 *
 * Voice is multipart, not JSON, so it cannot go through onboPost(): the file is
 * unpacked here and repacked with the session's user id added.
 */
export default defineEventHandler(async (event) => {
  const userId = await assistantUser(event)

  const parts = (await readMultipartFormData(event)) || []
  const audio = parts.find((p) => p.name === 'audio')
  if (!audio) throw createError({ statusCode: 400, message: 'audio is required' })

  const locale = parts.find((p) => p.name === 'locale')?.data?.toString() || 'ru'

  const form = new FormData()
  form.append('audio', new Blob([audio.data], { type: audio.type || 'audio/webm' }), audio.filename || 'voice.webm')
  form.append('user_id', userId)
  form.append('locale', locale)

  try {
    return await $fetch(`${onboBase()}/voice`, { method: 'POST', body: form, timeout: 60000 })
  } catch (err) {
    throw createError({
      statusCode: err.response?.status || 502,
      message: 'Assistant is unavailable',
    })
  }
})
