const API_BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:9950'

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    ...options,
  })

  if (!response.ok) {
    let detail = ''
    try {
      const body = await response.json()
      detail = body.error || body.detail || ''
      if (Array.isArray(detail)) {
        detail = detail.map((item) => item.msg || String(item)).join('; ')
      }
    } catch {
      /* 非 JSON 回應時略過 */
    }
    const message = detail
      ? `API 錯誤 ${response.status}: ${detail}`
      : `API 錯誤 ${response.status}: ${path}`
    throw new Error(message)
  }

  return response.json()
}

export function fetchStatus() {
  return request('/api/status')
}

export function fetchWechatAccount() {
  return request('/api/wechat/account')
}

export function fetchWechatLoginQrcode() {
  return request('/api/wechat/login/qrcode')
}

export function fetchWechatLoginStatus(uuid) {
  const params = new URLSearchParams({ uuid })
  return request(`/api/wechat/login/status?${params.toString()}`)
}

export function logoutWechatAccount() {
  return request('/api/wechat/logout', { method: 'POST', body: '{}' })
}

export function fetchLlmConfigs() {
  return request('/api/llm-configs')
}

export function createLlmConfig(payload) {
  return request('/api/llm-configs', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function activateLlmConfig(configId) {
  return request(`/api/llm-configs/${configId}/activate`, {
    method: 'PUT',
    body: '{}',
  })
}

export function deleteLlmConfig(configId) {
  return request(`/api/llm-configs/${configId}`, { method: 'DELETE' })
}

export function toggleAi(enabled) {
  return request('/api/toggle', {
    method: 'POST',
    body: JSON.stringify({ enabled }),
  })
}

export function fetchContacts() {
  return request('/api/contacts')
}

export function fetchMessages(limit = 50, wxId = null, keyword = null) {
  const params = new URLSearchParams({ limit: String(limit) })
  if (wxId) {
    params.set('wx_id', wxId)
  }
  if (keyword && keyword.trim()) {
    params.set('keyword', keyword.trim())
  }
  return request(`/api/messages?${params.toString()}`)
}

export function testLlm() {
  return request('/api/test_llm')
}

export function toggleContactAi(wxId, enabled) {
  return request(`/api/contacts/${encodeURIComponent(wxId)}/toggle_ai`, {
    method: 'POST',
    body: JSON.stringify({ enabled }),
  })
}

export function updateContactNickname(wxId, nickname) {
  return request(`/api/contacts/${encodeURIComponent(wxId)}/nickname`, {
    method: 'PUT',
    body: JSON.stringify({ nickname }),
  })
}

export function updateContactAssignedProfile(wxId, profileId) {
  return request(`/api/contacts/${encodeURIComponent(wxId)}/assigned_profile`, {
    method: 'POST',
    body: JSON.stringify({ profile_id: profileId }),
  })
}

export function updateContactMemory(wxId, memorySummary) {
  return request(`/api/contacts/${encodeURIComponent(wxId)}/memory`, {
    method: 'PUT',
    body: JSON.stringify({ memory_summary: memorySummary }),
  })
}

export function sendMessage(wxId, content, photoId = null) {
  const payload = {
    wx_id: wxId,
    content: content || '',
  }
  if (photoId != null) {
    payload.photo_id = photoId
  }
  return request('/api/send_message', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function sendMessageWithPaste(wxId, content, imageFile) {
  const formData = new FormData()
  formData.append('wx_id', wxId)
  formData.append('content', content || '')
  formData.append('file', imageFile, imageFile.name || 'screenshot.png')

  const response = await fetch(`${API_BASE}/api/send_message/paste`, {
    method: 'POST',
    body: formData,
  })

  if (!response.ok) {
    throw new Error(`API 錯誤 ${response.status}: /api/send_message/paste`)
  }

  return response.json()
}

export function getPhotoFileUrl(photoId) {
  return `${API_BASE}/api/photos/${photoId}/file`
}

export function fetchPhotos() {
  return request('/api/photos')
}

export async function uploadPhoto(file, name, keywords, promptHint = '', profileId = null) {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('name', name)
  formData.append('keywords', keywords)
  formData.append('prompt_hint', promptHint)
  if (profileId != null) {
    formData.append('profile_id', String(profileId))
  }

  const response = await fetch(`${API_BASE}/api/photos`, {
    method: 'POST',
    body: formData,
  })

  if (!response.ok) {
    throw new Error(`API 錯誤 ${response.status}: /api/photos`)
  }

  return response.json()
}

export function updatePhoto(photoId, payload) {
  return request(`/api/photos/${photoId}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function deletePhoto(photoId) {
  return request(`/api/photos/${photoId}`, {
    method: 'DELETE',
  })
}

export function fetchSystemPrompt() {
  return request('/api/system_prompt')
}

export function fetchSystemPrompts() {
  return request('/api/system_prompts')
}

export function createSystemPrompt(name, content) {
  return request('/api/system_prompts', {
    method: 'POST',
    body: JSON.stringify({ name, content }),
  })
}

export function updateSystemPrompt(promptId, payload) {
  return request(`/api/system_prompts/${promptId}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function activateSystemPrompt(promptId) {
  return request(`/api/system_prompts/${promptId}/activate`, {
    method: 'POST',
  })
}

export function deleteSystemPrompt(promptId) {
  return request(`/api/system_prompts/${promptId}`, {
    method: 'DELETE',
  })
}

export function saveSystemPrompt(content) {
  return request('/api/system_prompt', {
    method: 'POST',
    body: JSON.stringify({ content }),
  })
}
