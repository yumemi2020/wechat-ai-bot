<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  activateSystemPrompt,
  createSystemPrompt,
  deletePhoto,
  deleteSystemPrompt,
  fetchContacts,
  fetchMessages,
  fetchPhotos,
  fetchStatus,
  fetchSystemPrompts,
  fetchWechatAccount,
  fetchWechatLoginQrcode,
  fetchWechatLoginStatus,
  logoutWechatAccount,
  fetchLlmConfigs,
  createLlmConfig,
  activateLlmConfig,
  deleteLlmConfig,
  getPhotoFileUrl,
  sendMessage,
  sendMessageWithPaste,
  testLlm,
  toggleAi,
  toggleContactAi,
  updateContactAssignedProfile,
  updateContactMemory,
  updateContactNickname,
  updateSystemPrompt,
  uploadPhoto,
} from './api.js'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:9950'
const WS_BASE = import.meta.env.VITE_WS_BASE || API_BASE.replace(/^http/, 'ws')
const APP_VERSION = '測試版 v0.12'
const LLM_PROVIDER_PRESETS = {
  OpenAI: { base_url: 'https://api.openai.com/v1' },
  DeepSeek: { base_url: 'https://api.deepseek.com/v1' },
  Groq: { base_url: 'https://api.groq.com/openai/v1' },
  Ollama: { base_url: 'http://127.0.0.1:11434/v1' },
  Custom: { base_url: '' },
}
const USER_GUIDE_EMBED_URL = '/user-guide.html?embed=1'
const MANUAL_EMBED_URL = '/manual.html?embed=1'

const aiEnabled = ref(false)
const contacts = ref([])
const selectedContact = ref(null)
const messages = ref([])
const replyText = ref('')
const selectedReplyPhotoId = ref(null)
const pastedImageFile = ref(null)
const pastedImagePreviewUrl = ref('')
const photos = ref([])
const loadingPhotos = ref(false)

const showPhotoModal = ref(false)
const photoUploadName = ref('')
const photoUploadKeywords = ref('')
const photoUploadHint = ref('')
const photoUploadProfileId = ref(null)
const photoUploadFile = ref(null)
const savingPhoto = ref(false)
const photoToast = ref('')
const searchKeyword = ref('')
const activeSearchKeyword = ref('')
const isSearchMode = ref(false)
const searching = ref(false)
const loading = ref(true)
const messagesLoading = ref(false)
const toggling = ref(false)
const togglingContactAi = ref(false)
const refreshing = ref(false)
const sending = ref(false)
const testingLlm = ref(false)
const wsConnected = ref(false)
const error = ref('')

const showPromptModal = ref(false)
const systemPrompts = ref([])
const activePromptId = ref(null)
const selectedPromptId = ref(null)
const promptName = ref('')
const systemPromptText = ref('')
const loadingSystemPrompt = ref(false)
const savingSystemPrompt = ref(false)
const promptToast = ref('')

const showNicknameModal = ref(false)
const showManualModal = ref(false)
const manualTab = ref('user')
const showSystemModal = ref(false)
const imagePreviewUrl = ref('')
const wechatAccount = ref(null)
const loginQrBase64 = ref('')
const loginSessionUuid = ref('')
const loginStatusMessage = ref('')
const loginStatusKey = ref('idle')
const loadingWechatAccount = ref(false)
const generatingLoginQr = ref(false)
const loggingOutWechat = ref(false)
const llmConfigs = ref([])
const activeLlmConfigId = ref(null)
const loadingLlmConfigs = ref(false)
const savingLlmConfig = ref(false)
const llmForm = ref({
  name: '',
  provider: 'Custom',
  api_key: '',
  base_url: '',
  model_name: '',
  temperature: 0.7,
})
const systemToast = ref('')
const nicknameInput = ref('')
const memoryInput = ref('')
const savingMemory = ref(false)
const savingNickname = ref(false)
const selectedAssignProfileId = ref('')
const suppressAssignProfileChange = ref(false)
const assigningProfile = ref(false)

const isNewPrompt = computed(() => selectedPromptId.value === null)

const selectedPromptIsActive = computed(
  () =>
    !isNewPrompt.value &&
    selectedPromptId.value !== null &&
    selectedPromptId.value === activePromptId.value,
)

const chatContainer = ref(null)
let frontendSocket = null
let wsReconnectTimer = null
let loginPollTimer = null

const connectionState = computed(() => {
  if (loading.value) return 'connecting'
  if (error.value) return 'error'
  return 'connected'
})

const statusText = computed(() => {
  if (connectionState.value === 'connecting') return '連接中...'
  if (connectionState.value === 'error') return '連線異常'
  const aiText = aiEnabled.value ? 'AI 已開啟' : 'AI 已關閉'
  return wsConnected.value ? aiText : `${aiText} · 推播重連中`
})

const statusBadgeClass = computed(() => {
  if (connectionState.value === 'connecting') return 'bg-yellow-100 text-yellow-800'
  if (connectionState.value === 'error') return 'bg-red-100 text-red-800'
  if (!wsConnected.value) return 'bg-orange-100 text-orange-800'
  return aiEnabled.value ? 'bg-green-100 text-green-800' : 'bg-gray-200 text-gray-700'
})

const displayMessages = computed(() =>
  [...messages.value].sort(
    (a, b) => new Date(a.created_at) - new Date(b.created_at),
  ),
)

const selectedContactAiEnabled = computed(() => {
  const contact = contacts.value.find((c) => c.wx_id === selectedContact.value)
  return contact?.ai_enabled ?? false
})

const selectedContactRecord = computed(() =>
  contacts.value.find((c) => c.wx_id === selectedContact.value) ?? null,
)

const selectedContactDisplayName = computed(() =>
  getContactDisplayName(selectedContactRecord.value),
)

const selectedContactProfileName = computed(() => {
  const record = selectedContactRecord.value
  if (!record) return null
  if (record.profile_name?.trim()) {
    return record.profile_name.trim()
  }
  const active = systemPrompts.value.find((p) => p.id === activePromptId.value)
  return active?.name || '預設人設'
})

const replyPhotoOptions = computed(() => {
  const record = selectedContactRecord.value
  const profileId = record?.assigned_profile_id ?? activePromptId.value
  if (!profileId) {
    return photos.value.filter((photo) => !photo.profile_id)
  }
  return photos.value.filter((photo) => photo.profile_id === profileId)
})

function getContactDisplayName(contact) {
  if (!contact) return ''
  const nickname = contact.nickname?.trim()
  return nickname || contact.wx_id
}

const showManualInput = computed(
  () =>
    selectedContact.value &&
    (!selectedContactAiEnabled.value || !aiEnabled.value),
)

const canManualReply = computed(
  () =>
    showManualInput.value &&
    !sending.value &&
    (replyText.value.trim().length > 0 ||
      selectedReplyPhotoId.value != null ||
      pastedImageFile.value != null),
)

const emptyMessageText = computed(() => {
  if (isSearchMode.value && activeSearchKeyword.value) {
    return `找不到含有「${activeSearchKeyword.value}」的訊息`
  }
  return '此聯絡人尚無對話紀錄'
})

function formatTime(isoString) {
  if (!isoString) return ''
  const date = new Date(isoString)
  return date.toLocaleString('zh-TW', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

function formatContactTime(isoString) {
  if (!isoString) return ''
  const date = new Date(isoString)
  const now = new Date()
  const isToday = date.toDateString() === now.toDateString()
  if (isToday) {
    return date.toLocaleTimeString('zh-TW', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
  }
  return date.toLocaleDateString('zh-TW', {
    month: '2-digit',
    day: '2-digit',
  })
}

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

const IMAGE_MSG_PATTERN = /\[IMAGE_MSG\]([^\s\n]+)/g
const MEDIA_MSG_PATTERN = /\[(IMAGE_MSG|VOICE_MSG)\]([^\s\n]+)/g

function resolveMediaUrl(path) {
  if (!path) return ''
  if (path.startsWith('http://') || path.startsWith('https://')) return path
  if (path.startsWith('/')) return `${API_BASE}${path}`
  return `${API_BASE}/${path}`
}

const resolveImageUrl = resolveMediaUrl

function parseMessageSegments(content) {
  const hasImage = content?.includes('[IMAGE_MSG]')
  const hasVoice = content?.includes('[VOICE_MSG]')
  if (!hasImage && !hasVoice) {
    if (content?.includes('[圖片訊息]') || content?.includes('[語音訊息]')) {
      return [{ type: 'text', text: content }]
    }
    return [{ type: 'text', text: content || '' }]
  }

  const segments = []
  let lastIndex = 0
  const regex = new RegExp(MEDIA_MSG_PATTERN.source, 'g')
  let match = regex.exec(content)

  while (match) {
    if (match.index > lastIndex) {
      const textPart = content.slice(lastIndex, match.index)
      if (textPart) {
        segments.push({ type: 'text', text: textPart })
      }
    }
    const tag = match[1]
    const url = resolveMediaUrl(match[2])
    segments.push({
      type: tag === 'VOICE_MSG' ? 'voice' : 'image',
      url,
    })
    lastIndex = match.index + match[0].length
    match = regex.exec(content)
  }

  if (lastIndex < content.length) {
    const tail = content.slice(lastIndex)
    if (tail) {
      segments.push({ type: 'text', text: tail })
    }
  }

  return segments.length > 0 ? segments : [{ type: 'text', text: content }]
}

function messageHasImage(content) {
  return Boolean(content && content.includes('[IMAGE_MSG]'))
}

function messageHasVoice(content) {
  return Boolean(content && content.includes('[VOICE_MSG]'))
}

function messageHasMedia(content) {
  return messageHasImage(content) || messageHasVoice(content)
}

function openImagePreview(url) {
  if (!url) return
  imagePreviewUrl.value = url
}

function closeImagePreview() {
  imagePreviewUrl.value = ''
}

function handleImagePreviewKeydown(event) {
  if (event.key === 'Escape') {
    closeImagePreview()
  }
}

function getHighlightParts(content, keyword) {
  if (!keyword?.trim()) {
    return [{ text: content, highlight: false }]
  }

  const kw = keyword.trim()
  const regex = new RegExp(`(${escapeRegExp(kw)})`, 'gi')
  return content
    .split(regex)
    .filter((part) => part.length > 0)
    .map((part) => ({
      text: part,
      highlight: part.toLowerCase() === kw.toLowerCase(),
    }))
}

async function scrollToBottom() {
  await nextTick()
  requestAnimationFrame(() => {
    if (chatContainer.value) {
      chatContainer.value.scrollTop = chatContainer.value.scrollHeight
    }
  })
}

async function scrollToBottomAfterRender() {
  await nextTick()
  await nextTick()
  requestAnimationFrame(() => {
    if (chatContainer.value) {
      chatContainer.value.scrollTop = chatContainer.value.scrollHeight
    }
  })
}

function clearSearchState() {
  searchKeyword.value = ''
  activeSearchKeyword.value = ''
  isSearchMode.value = false
}

function appendMessagesIfNew(messageList) {
  let added = false
  for (const msg of messageList || []) {
    if (appendMessageIfNew(msg)) {
      added = true
    }
  }
  return added
}

function appendMessageIfNew(msg) {
  if (!msg?.id) return false
  if (messages.value.some((item) => item.id === msg.id)) return false
  messages.value.push(msg)
  return true
}

async function handleRealtimeMessage(msg) {
  if (msg.wx_id === selectedContact.value) {
    if (isSearchMode.value) {
      return
    }
    const added = appendMessageIfNew(msg)
    if (added) {
      await scrollToBottom()
    }
    return
  }

  try {
    await loadContacts()
  } catch (err) {
    console.error('更新聯絡人列表失敗', err)
  }
}

function handleMessageUpdated(msg) {
  if (!msg?.id) return
  const index = messages.value.findIndex((item) => item.id === msg.id)
  if (index !== -1) {
    messages.value[index] = msg
  } else if (msg.wx_id === selectedContact.value) {
    messages.value.push(msg)
  }
}

function connectFrontendWebSocket() {
  if (frontendSocket) {
    frontendSocket.close()
    frontendSocket = null
  }

  const ws = new WebSocket(`${WS_BASE}/ws/frontend`)
  frontendSocket = ws

  ws.onopen = () => {
    wsConnected.value = true
    ws.send('ping')
  }

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      if (data.type === 'new_message' && data.message) {
        handleRealtimeMessage(data.message)
      } else if (data.type === 'message_updated' && data.message) {
        handleMessageUpdated(data.message)
      }
    } catch (err) {
      console.error('解析 WebSocket 訊息失敗', err)
    }
  }

  ws.onclose = () => {
    wsConnected.value = false
    frontendSocket = null
    if (!wsReconnectTimer) {
      wsReconnectTimer = setTimeout(() => {
        wsReconnectTimer = null
        connectFrontendWebSocket()
      }, 3000)
    }
  }

  ws.onerror = () => {
    ws.close()
  }
}

function disconnectFrontendWebSocket() {
  if (wsReconnectTimer) {
    clearTimeout(wsReconnectTimer)
    wsReconnectTimer = null
  }
  if (frontendSocket) {
    frontendSocket.close()
    frontendSocket = null
  }
  wsConnected.value = false
}

async function loadStatus() {
  const data = await fetchStatus()
  aiEnabled.value = data.ai_enabled
}

async function loadContacts() {
  const data = await fetchContacts()
  contacts.value = data.contacts || []
}

async function loadMessagesForContact(wxId, keyword = null) {
  if (!wxId) {
    messages.value = []
    return
  }

  messagesLoading.value = true
  try {
    const data = await fetchMessages(keyword ? 100 : 50, wxId, keyword)
    messages.value = data.messages || []
  } finally {
    messagesLoading.value = false
  }
  await scrollToBottomAfterRender()
}

async function loadSystemPromptsForUi() {
  const data = await fetchSystemPrompts()
  systemPrompts.value = data.prompts || []
  activePromptId.value = data.active_id ?? null
}

function openManual() {
  manualTab.value = 'user'
  showManualModal.value = true
}

function closeManual() {
  showManualModal.value = false
}

const manualEmbedUrl = computed(() =>
  manualTab.value === 'user' ? USER_GUIDE_EMBED_URL : MANUAL_EMBED_URL,
)

function stopLoginPolling() {
  if (loginPollTimer) {
    clearInterval(loginPollTimer)
    loginPollTimer = null
  }
}

function showSystemMessage(message, isSuccess = true) {
  systemToast.value = message
  if (isSuccess) {
    setTimeout(() => {
      if (systemToast.value === message) {
        systemToast.value = ''
      }
    }, 3000)
  }
}

async function loadWechatAccount() {
  loadingWechatAccount.value = true
  try {
    const data = await fetchWechatAccount()
    wechatAccount.value = data.account || null
  } catch (err) {
    showSystemMessage(err.message || '載入微信帳號失敗', false)
  } finally {
    loadingWechatAccount.value = false
  }
}

async function pollLoginStatus() {
  if (!loginSessionUuid.value) return

  try {
    const data = await fetchWechatLoginStatus(loginSessionUuid.value)
    loginStatusMessage.value = data.message || ''
    loginStatusKey.value = data.status || (data.success ? 'success' : 'pending')

    if (data.success && data.status === 'success') {
      stopLoginPolling()
      await loadWechatAccount()
      await loadContacts()
      showSystemMessage(`登入成功：${data.nickname || data.wxid || '微信帳號'}`)
      loginQrBase64.value = ''
      loginSessionUuid.value = ''
      loginStatusKey.value = 'success'
      return
    }

    if (data.status === 'expired') {
      stopLoginPolling()
      loginQrBase64.value = ''
      loginSessionUuid.value = ''
      showSystemMessage(data.message || 'QR Code 已過期', false)
    }
  } catch (err) {
    loginStatusMessage.value = err.message || '輪詢登入狀態失敗'
    loginStatusKey.value = 'error'
  }
}

function startLoginPolling() {
  stopLoginPolling()
  loginPollTimer = setInterval(() => {
    pollLoginStatus()
  }, 3000)
}

async function handleGenerateLoginQrcode() {
  if (generatingLoginQr.value) return

  generatingLoginQr.value = true
  systemToast.value = ''
  loginStatusMessage.value = ''
  loginStatusKey.value = 'pending'
  stopLoginPolling()

  try {
    const data = await fetchWechatLoginQrcode()
    loginSessionUuid.value = data.uuid
    loginQrBase64.value = data.qr_base64
    loginStatusMessage.value = '請使用微信掃描 QR Code'
    await pollLoginStatus()
    startLoginPolling()
  } catch (err) {
    loginQrBase64.value = ''
    loginSessionUuid.value = ''
    showSystemMessage(err.message || '產生 QR Code 失敗', false)
  } finally {
    generatingLoginQr.value = false
  }
}

async function handleLogoutWechat() {
  if (loggingOutWechat.value || generatingLoginQr.value) return
  if (!window.confirm('確定要登出目前微信帳號嗎？登出後需重新掃碼才能收發訊息。')) {
    return
  }

  loggingOutWechat.value = true
  systemToast.value = ''
  stopLoginPolling()
  loginQrBase64.value = ''
  loginSessionUuid.value = ''

  try {
    const data = await logoutWechatAccount()
    await loadWechatAccount()
    showSystemMessage(data.message || '已登出微信帳號')
    loginStatusKey.value = 'idle'
    loginStatusMessage.value = ''
  } catch (err) {
    showSystemMessage(err.message || '登出失敗', false)
  } finally {
    loggingOutWechat.value = false
  }
}

async function loadLlmConfigs() {
  loadingLlmConfigs.value = true
  try {
    const data = await fetchLlmConfigs()
    llmConfigs.value = data.configs || []
    activeLlmConfigId.value = data.active_id ?? null
  } catch (err) {
    showSystemMessage(err.message || '載入 LLM 設定失敗', false)
  } finally {
    loadingLlmConfigs.value = false
  }
}

function handleLlmProviderChange() {
  const preset = LLM_PROVIDER_PRESETS[llmForm.value.provider]
  if (preset?.base_url) {
    llmForm.value.base_url = preset.base_url
  }
}

function resetLlmForm() {
  llmForm.value = {
    name: '',
    provider: 'Custom',
    api_key: '',
    base_url: '',
    model_name: '',
    temperature: 0.7,
  }
}

async function handleCreateLlmConfig() {
  if (savingLlmConfig.value) return

  const form = llmForm.value
  if (!form.name.trim() || !form.base_url.trim() || !form.model_name.trim()) {
    showSystemMessage('請填寫設定名稱、Base URL 與 Model Name', false)
    return
  }

  savingLlmConfig.value = true
  try {
    await createLlmConfig({
      name: form.name.trim(),
      provider: form.provider,
      api_key: form.api_key.trim() || null,
      base_url: form.base_url.trim(),
      model_name: form.model_name.trim(),
      temperature: Number(form.temperature),
    })
    await loadLlmConfigs()
    resetLlmForm()
    showSystemMessage('LLM 設定已新增')
  } catch (err) {
    showSystemMessage(err.message || '新增 LLM 設定失敗', false)
  } finally {
    savingLlmConfig.value = false
  }
}

async function handleActivateLlmConfig(configId) {
  if (savingLlmConfig.value) return
  savingLlmConfig.value = true
  try {
    await activateLlmConfig(configId)
    await loadLlmConfigs()
    showSystemMessage('已切換啟用中的 LLM 模型')
  } catch (err) {
    showSystemMessage(err.message || '啟用 LLM 設定失敗', false)
  } finally {
    savingLlmConfig.value = false
  }
}

async function handleDeleteLlmConfig(config) {
  if (savingLlmConfig.value) return
  if (config.is_active) {
    showSystemMessage('無法刪除使用中的設定，請先啟用其他模型', false)
    return
  }
  if (!window.confirm(`確定刪除「${config.name}」？`)) return

  savingLlmConfig.value = true
  try {
    await deleteLlmConfig(config.id)
    await loadLlmConfigs()
    showSystemMessage('LLM 設定已刪除')
  } catch (err) {
    showSystemMessage(err.message || '刪除 LLM 設定失敗', false)
  } finally {
    savingLlmConfig.value = false
  }
}

async function openSystemModal() {
  showSystemModal.value = true
  systemToast.value = ''
  await Promise.all([loadWechatAccount(), loadLlmConfigs()])
}

function closeSystemModal() {
  if (generatingLoginQr.value || loggingOutWechat.value || savingLlmConfig.value) return
  stopLoginPolling()
  showSystemModal.value = false
  systemToast.value = ''
}

async function initDashboard() {
  loading.value = true
  error.value = ''
  try {
    await Promise.all([loadStatus(), loadContacts(), loadSystemPromptsForUi()])
    connectFrontendWebSocket()
  } catch (err) {
    error.value = err.message || '無法連線至後端 API'
  } finally {
    loading.value = false
  }
}

function selectContact(wxId) {
  selectedContact.value = wxId
}

async function handleSearch() {
  const keyword = searchKeyword.value.trim()
  if (!selectedContact.value || !keyword || searching.value) return

  searching.value = true
  error.value = ''
  try {
    activeSearchKeyword.value = keyword
    isSearchMode.value = true
    await loadMessagesForContact(selectedContact.value, keyword)
  } catch (err) {
    error.value = err.message || '搜尋失敗'
  } finally {
    searching.value = false
  }
}

async function handleClearSearch() {
  if (!selectedContact.value) return

  clearSearchState()
  error.value = ''
  try {
    await loadMessagesForContact(selectedContact.value)
  } catch (err) {
    error.value = err.message || '載入對話紀錄失敗'
  }
}

function handleSearchKeydown(event) {
  if (event.key === 'Enter') {
    event.preventDefault()
    handleSearch()
  }
}

async function handleToggle() {
  if (toggling.value || connectionState.value === 'error') return

  const nextValue = !aiEnabled.value
  toggling.value = true
  error.value = ''

  try {
    const data = await toggleAi(nextValue)
    aiEnabled.value = data.ai_enabled
  } catch (err) {
    error.value = err.message || '切換 AI 狀態失敗'
  } finally {
    toggling.value = false
  }
}

async function handleContactAiToggle() {
  if (!selectedContact.value || togglingContactAi.value) return

  const nextValue = !selectedContactAiEnabled.value
  togglingContactAi.value = true
  error.value = ''

  try {
    const data = await toggleContactAi(selectedContact.value, nextValue)
    await loadContacts()
    const contact = contacts.value.find((c) => c.wx_id === selectedContact.value)
    if (contact) {
      contact.ai_enabled = data.ai_enabled
      syncSelectedAssignProfileId(contact)
    }
  } catch (err) {
    error.value = err.message || '切換聯絡人 AI 狀態失敗'
  } finally {
    togglingContactAi.value = false
  }
}

function syncSelectedMemorySummary(contact) {
  memoryInput.value = contact?.memory_summary?.trim() || ''
}

async function handleSaveMemory() {
  if (!selectedContact.value || savingMemory.value) return

  savingMemory.value = true
  error.value = ''
  try {
    const payload = memoryInput.value.trim()
    await updateContactMemory(selectedContact.value, payload || null)
    await loadContacts()
    const contact = contacts.value.find((c) => c.wx_id === selectedContact.value)
    syncSelectedMemorySummary(contact)
  } catch (err) {
    error.value = err.message || '儲存長期記憶失敗'
  } finally {
    savingMemory.value = false
  }
}

function syncSelectedAssignProfileId(contact) {
  suppressAssignProfileChange.value = true
  if (!contact) {
    selectedAssignProfileId.value = ''
    nextTick(() => {
      suppressAssignProfileChange.value = false
    })
    return
  }
  const assignedId = contact.assigned_profile_id
  if (
    assignedId != null &&
    systemPrompts.value.some((prompt) => prompt.id === assignedId)
  ) {
    selectedAssignProfileId.value = String(assignedId)
  } else {
    selectedAssignProfileId.value = ''
  }
  nextTick(() => {
    suppressAssignProfileChange.value = false
  })
}

async function handleAssignProfileChange() {
  if (
    suppressAssignProfileChange.value ||
    !selectedContact.value ||
    assigningProfile.value
  ) {
    return
  }

  assigningProfile.value = true
  error.value = ''
  try {
    const profileId =
      selectedAssignProfileId.value === ''
        ? null
        : Number(selectedAssignProfileId.value)
    if (profileId != null && !Number.isFinite(profileId)) {
      throw new Error('請選擇有效的人設')
    }
    await updateContactAssignedProfile(selectedContact.value, profileId)
    await loadContacts()
    const contact = contacts.value.find((c) => c.wx_id === selectedContact.value)
    syncSelectedAssignProfileId(contact)
  } catch (err) {
    error.value = err.message || '更新綁定人設失敗'
  } finally {
    assigningProfile.value = false
  }
}

async function handleRefresh() {
  if (!selectedContact.value) return

  refreshing.value = true
  error.value = ''
  try {
    if (isSearchMode.value && activeSearchKeyword.value) {
      await Promise.all([
        loadContacts(),
        loadMessagesForContact(selectedContact.value, activeSearchKeyword.value),
      ])
    } else {
      await Promise.all([
        loadContacts(),
        loadMessagesForContact(selectedContact.value),
      ])
    }
  } catch (err) {
    error.value = err.message || '重新整理失敗'
  } finally {
    refreshing.value = false
  }
}

function openNicknameModal() {
  if (!selectedContact.value) return
  nicknameInput.value = selectedContactRecord.value?.nickname?.trim() || ''
  showNicknameModal.value = true
}

function closeNicknameModal() {
  if (savingNickname.value) return
  showNicknameModal.value = false
}

async function handleSaveNickname() {
  if (!selectedContact.value || savingNickname.value) return

  savingNickname.value = true
  error.value = ''
  try {
    await updateContactNickname(selectedContact.value, nicknameInput.value.trim())
    await loadContacts()
    showNicknameModal.value = false
  } catch (err) {
    error.value = err.message || '更新暱稱失敗'
  } finally {
    savingNickname.value = false
  }
}

async function handleTestLlm() {
  if (testingLlm.value) return

  testingLlm.value = true
  try {
    const data = await testLlm()
    if (data.success) {
      const configHint = data.config?.name ? `（${data.config.name}）` : ''
      alert(`✅ AI 連線成功${configHint}！回覆：${data.message}`)
    } else {
      alert(`❌ AI 連線失敗，錯誤原因：${data.error}`)
    }
  } catch (err) {
    alert(`❌ AI 連線失敗，錯誤原因：${err.message}`)
  } finally {
    testingLlm.value = false
  }
}

async function loadSystemPrompts(selectId = null) {
  const data = await fetchSystemPrompts()
  systemPrompts.value = data.prompts || []
  activePromptId.value = data.active_id ?? null

  const targetId = selectId ?? activePromptId.value
  if (targetId != null) {
    selectPromptById(targetId)
  } else if (systemPrompts.value.length > 0) {
    selectPromptById(systemPrompts.value[0].id)
  } else {
    startNewPrompt()
  }
}

function selectPromptById(promptId) {
  const prompt = systemPrompts.value.find((item) => item.id === promptId)
  if (!prompt) return
  selectedPromptId.value = prompt.id
  promptName.value = prompt.name
  systemPromptText.value = prompt.content
}

function startNewPrompt() {
  selectedPromptId.value = null
  promptName.value = ''
  systemPromptText.value = ''
}

function showPromptMessage(message, isSuccess = true) {
  promptToast.value = message
  if (isSuccess) {
    setTimeout(() => {
      if (promptToast.value === message) {
        promptToast.value = ''
      }
    }, 2500)
  }
}

async function openPromptModal() {
  showPromptModal.value = true
  promptToast.value = ''
  loadingSystemPrompt.value = true
  try {
    await loadSystemPrompts()
  } catch (err) {
    error.value = err.message || '載入人設失敗'
    showPromptModal.value = false
  } finally {
    loadingSystemPrompt.value = false
  }
}

function closePromptModal() {
  if (savingSystemPrompt.value) return
  showPromptModal.value = false
  promptToast.value = ''
}

async function handleSaveSystemPrompt() {
  const name = promptName.value.trim()
  const content = systemPromptText.value.trim()
  if (!name || !content || savingSystemPrompt.value) return

  savingSystemPrompt.value = true
  promptToast.value = ''
  try {
    if (isNewPrompt.value) {
      const data = await createSystemPrompt(name, content)
      await loadSystemPrompts(data.prompt.id)
      showPromptMessage('人設已建立')
    } else {
      await updateSystemPrompt(selectedPromptId.value, { name, content })
      await loadSystemPrompts(selectedPromptId.value)
      showPromptMessage('人設已儲存')
    }
  } catch (err) {
    showPromptMessage(err.message || '儲存失敗', false)
  } finally {
    savingSystemPrompt.value = false
  }
}

async function handleActivateSystemPrompt() {
  if (isNewPrompt.value || savingSystemPrompt.value) return

  const name = promptName.value.trim()
  const content = systemPromptText.value.trim()
  if (!name || !content) return

  savingSystemPrompt.value = true
  promptToast.value = ''
  try {
    const current = systemPrompts.value.find((item) => item.id === selectedPromptId.value)
    const hasChanges =
      current &&
      (name !== current.name || content !== current.content)

    if (hasChanges) {
      await updateSystemPrompt(selectedPromptId.value, { name, content })
    }

    await activateSystemPrompt(selectedPromptId.value)
    await loadSystemPrompts(selectedPromptId.value)
    showPromptMessage('已套用此人設')
  } catch (err) {
    showPromptMessage(err.message || '套用失敗', false)
  } finally {
    savingSystemPrompt.value = false
  }
}

async function handleDeleteSystemPrompt() {
  if (isNewPrompt.value || savingSystemPrompt.value) return
  if (!window.confirm(`確定要刪除「${promptName.value}」嗎？`)) return

  savingSystemPrompt.value = true
  promptToast.value = ''
  try {
    await deleteSystemPrompt(selectedPromptId.value)
    await loadSystemPrompts()
    showPromptMessage('人設已刪除')
  } catch (err) {
    showPromptMessage(err.message || '刪除失敗', false)
  } finally {
    savingSystemPrompt.value = false
  }
}

async function loadPhotos() {
  loadingPhotos.value = true
  try {
    const data = await fetchPhotos()
    photos.value = data.photos || []
  } catch (err) {
    error.value = err.message || '載入圖片庫失敗'
  } finally {
    loadingPhotos.value = false
  }
}

function onPhotoFileChange(event) {
  const file = event.target.files?.[0]
  photoUploadFile.value = file || null
  if (file && !photoUploadName.value) {
    photoUploadName.value = file.name.replace(/\.[^.]+$/, '')
  }
}

async function openPhotoModal() {
  showPhotoModal.value = true
  photoToast.value = ''
  if (!systemPrompts.value.length) {
    await loadSystemPromptsForUi()
  }
  photoUploadProfileId.value = activePromptId.value
  await loadPhotos()
}

function closePhotoModal() {
  if (savingPhoto.value) return
  showPhotoModal.value = false
  photoToast.value = ''
  photoUploadFile.value = null
  photoUploadName.value = ''
  photoUploadKeywords.value = ''
  photoUploadHint.value = ''
  photoUploadProfileId.value = null
}

async function handleUploadPhoto() {
  const name = photoUploadName.value.trim()
  const keywords = photoUploadKeywords.value.trim()
  if (
    !photoUploadFile.value ||
    !name ||
    !keywords ||
    photoUploadProfileId.value == null ||
    savingPhoto.value
  ) {
    return
  }

  savingPhoto.value = true
  photoToast.value = ''
  try {
    await uploadPhoto(
      photoUploadFile.value,
      name,
      keywords,
      photoUploadHint.value.trim(),
      photoUploadProfileId.value,
    )
    photoUploadFile.value = null
    photoUploadName.value = ''
    photoUploadKeywords.value = ''
    photoUploadHint.value = ''
    await loadPhotos()
    photoToast.value = '圖片已上傳'
  } catch (err) {
    photoToast.value = err.message || '上傳失敗'
  } finally {
    savingPhoto.value = false
  }
}

async function handleDeletePhoto(photoId, photoName) {
  if (!window.confirm(`確定要刪除「${photoName}」嗎？`)) return
  savingPhoto.value = true
  try {
    await deletePhoto(photoId)
    if (selectedReplyPhotoId.value === photoId) {
      selectedReplyPhotoId.value = null
    }
    await loadPhotos()
    photoToast.value = '圖片已刪除'
  } catch (err) {
    photoToast.value = err.message || '刪除失敗'
  } finally {
    savingPhoto.value = false
  }
}

function clearPastedImage() {
  if (pastedImagePreviewUrl.value) {
    URL.revokeObjectURL(pastedImagePreviewUrl.value)
  }
  pastedImageFile.value = null
  pastedImagePreviewUrl.value = ''
}

function setPastedImage(file) {
  clearPastedImage()
  pastedImageFile.value = file
  pastedImagePreviewUrl.value = URL.createObjectURL(file)
  selectedReplyPhotoId.value = null
}

function handleReplyPaste(event) {
  const items = event.clipboardData?.items
  if (!items) return

  for (const item of items) {
    if (!item.type.startsWith('image/')) continue

    const file = item.getAsFile()
    if (!file) continue

    event.preventDefault()
    const ext = item.type.split('/')[1] || 'png'
    const namedFile = new File(
      [file],
      `screenshot-${Date.now()}.${ext}`,
      { type: item.type },
    )
    setPastedImage(namedFile)
    return
  }
}

function handleReplyPhotoSelect() {
  if (selectedReplyPhotoId.value != null) {
    clearPastedImage()
  }
}

async function handleSendReply() {
  const text = replyText.value.trim()
  const photoId = selectedReplyPhotoId.value
  const pastedFile = pastedImageFile.value
  if ((!text && photoId == null && !pastedFile) || !selectedContact.value || sending.value) {
    return
  }

  sending.value = true
  error.value = ''

  try {
    const data = pastedFile
      ? await sendMessageWithPaste(selectedContact.value, text, pastedFile)
      : await sendMessage(selectedContact.value, text, photoId)
    if (!data.success) {
      throw new Error(data.error || '發送失敗')
    }

    replyText.value = ''
    selectedReplyPhotoId.value = null
    clearPastedImage()

    if (!isSearchMode.value) {
      if (data.messages?.length) {
        appendMessagesIfNew(data.messages)
      } else if (data.message) {
        appendMessageIfNew(data.message)
      }
      await scrollToBottom()
    }

    await loadContacts()
  } catch (err) {
    error.value = err.message || '發送訊息失敗'
  } finally {
    sending.value = false
  }
}

function handleReplyKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    handleSendReply()
  }
}

watch(selectedContact, async (wxId) => {
  replyText.value = ''
  clearSearchState()
  if (!wxId) {
    messages.value = []
    selectedAssignProfileId.value = ''
    memoryInput.value = ''
    return
  }
  const contact = contacts.value.find((c) => c.wx_id === wxId) ?? null
  syncSelectedAssignProfileId(contact)
  syncSelectedMemorySummary(contact)
  error.value = ''
  try {
    await loadMessagesForContact(wxId)
  } catch (err) {
    error.value = err.message || '載入對話紀錄失敗'
  }
})

watch(
  () => [messages.value.length, messagesLoading.value, selectedContact.value],
  async ([, loading]) => {
    if (!loading && selectedContact.value && !isSearchMode.value) {
      await scrollToBottomAfterRender()
    }
  },
)

onMounted(() => {
  initDashboard()
  loadPhotos()
  window.addEventListener('keydown', handleImagePreviewKeydown)
})

onUnmounted(() => {
  stopLoginPolling()
  disconnectFrontendWebSocket()
  clearPastedImage()
  window.removeEventListener('keydown', handleImagePreviewKeydown)
})
</script>

<template>
  <div class="flex h-screen flex-col bg-[#ededed]">
    <!-- 頂部全域控制列 -->
    <header class="z-10 shrink-0 border-b border-gray-200 bg-white shadow-sm">
      <div class="flex items-center justify-between px-6 py-3">
        <div>
          <div class="flex flex-wrap items-center gap-2">
            <h1 class="text-lg font-semibold text-gray-900">WeChat AI Bot 控制台</h1>
            <button
              type="button"
              class="rounded-full border border-indigo-200 bg-indigo-50 px-2.5 py-0.5 text-xs font-medium text-indigo-700 transition hover:border-indigo-300 hover:bg-indigo-100"
              :title="'開啟使用指南（' + APP_VERSION + '）'"
              @click="openManual"
            >
              {{ APP_VERSION }}
            </button>
          </div>
          <p class="text-xs text-gray-500">雙欄式對話管理介面 · 即時推播 · 點版本號開啟使用指南</p>
        </div>

        <div class="flex items-center gap-3">
          <span
            class="rounded-full px-3 py-1 text-sm font-medium"
            :class="statusBadgeClass"
          >
            {{ statusText }}
          </span>

          <button
            type="button"
            class="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="loading"
            @click="openPhotoModal"
          >
            📷 圖片庫
          </button>

          <button
            type="button"
            class="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="loading"
            @click="openSystemModal"
          >
            🛠️ 系統設定
          </button>

          <button
            type="button"
            class="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="loading"
            @click="openPromptModal"
          >
            ⚙️ 人設設定
          </button>

          <button
            type="button"
            class="inline-flex items-center gap-1.5 rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5 text-sm font-medium text-amber-800 transition hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="testingLlm || loading"
            @click="handleTestLlm"
          >
            <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M13 10V3L4 14h7v7l9-11h-7z"
              />
            </svg>
            {{ testingLlm ? '測試中...' : '測試 AI 連線' }}
          </button>

          <button
            type="button"
            role="switch"
            :aria-checked="aiEnabled"
            :disabled="loading || toggling || connectionState === 'error'"
            class="relative inline-flex h-7 w-12 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-green-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
            :class="aiEnabled ? 'bg-green-500' : 'bg-gray-300'"
            @click="handleToggle"
          >
            <span
              class="pointer-events-none inline-block h-6 w-6 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out"
              :class="aiEnabled ? 'translate-x-5' : 'translate-x-0'"
            />
          </button>
        </div>
      </div>

      <div v-if="error" class="px-6 pb-3">
        <p class="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600">{{ error }}</p>
      </div>
    </header>

    <!-- 雙欄主體 -->
    <div class="flex min-h-0 flex-1">
      <!-- 左側聯絡人列表 -->
      <aside class="flex w-1/3 min-w-[260px] max-w-sm flex-col border-r border-gray-200 bg-white">
        <div class="border-b border-gray-100 px-4 py-3">
          <h2 class="text-sm font-semibold text-gray-700">聯絡人</h2>
          <p class="text-xs text-gray-400">共 {{ contacts.length }} 位</p>
        </div>

        <div v-if="loading" class="flex flex-1 items-center justify-center text-sm text-gray-400">
          載入中...
        </div>

        <div
          v-else-if="contacts.length === 0"
          class="flex flex-1 items-center justify-center px-4 text-center text-sm text-gray-400"
        >
          尚無聯絡人紀錄
        </div>

        <ul v-else class="flex-1 overflow-y-auto">
          <li
            v-for="contact in contacts"
            :key="contact.wx_id"
            class="cursor-pointer border-b border-gray-50 px-4 py-3 transition hover:bg-gray-50"
            :class="selectedContact === contact.wx_id ? 'bg-green-50 hover:bg-green-50' : ''"
            @click="selectContact(contact.wx_id)"
          >
            <div class="flex items-center justify-between gap-2">
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-2">
                  <p
                    class="truncate text-sm font-medium"
                    :class="selectedContact === contact.wx_id ? 'text-green-800' : 'text-gray-900'"
                  >
                    {{ getContactDisplayName(contact) }}
                  </p>
                  <span
                    v-if="contact.ai_enabled"
                    class="inline-flex shrink-0 items-center gap-0.5 rounded-full bg-green-100 px-1.5 py-0.5 text-[10px] font-medium text-green-700"
                    title="AI 名單"
                  >
                    <svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                    </svg>
                    AI
                  </span>
                  <span
                    v-else
                    class="inline-flex shrink-0 items-center gap-0.5 rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-500"
                    title="一般名單"
                  >
                    <svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                    </svg>
                    一般
                  </span>
                </div>
                <p
                  v-if="contact.nickname?.trim()"
                  class="mt-0.5 truncate text-xs text-gray-400"
                >
                  {{ contact.wx_id }}
                </p>
              </div>
              <span class="shrink-0 text-xs text-gray-400">
                {{ formatContactTime(contact.last_time) }}
              </span>
            </div>
          </li>
        </ul>
      </aside>

      <!-- 右側對話面板 -->
      <main class="flex w-2/3 flex-1 flex-col bg-[#ededed]">
        <!-- 對話標題列 -->
        <div
          v-if="selectedContact"
          class="border-b border-gray-200 bg-[#f7f7f7] px-5 py-3"
        >
          <div class="flex items-center justify-between gap-4">
            <div class="min-w-0 flex-1">
              <div class="flex flex-wrap items-center gap-2">
                <h2 class="text-sm font-semibold text-gray-900">
                  {{ selectedContactDisplayName }}
                </h2>
                <button
                  type="button"
                  class="rounded-md px-1.5 py-0.5 text-xs text-gray-500 transition hover:bg-gray-200 hover:text-gray-800"
                  title="編輯暱稱"
                  @click="openNicknameModal"
                >
                  ✏️ 編輯名稱
                </button>
                <span
                  v-if="selectedContactAiEnabled"
                  class="rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700"
                >
                  AI 處理中
                </span>
                <span
                  v-else
                  class="rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-600"
                >
                  一般名單
                </span>
                <span
                  class="rounded-full bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-700"
                >
                  🤖 負責專員：{{ selectedContactProfileName }}
                </span>
              </div>
              <p class="text-xs text-gray-500">
                <span
                  v-if="selectedContactRecord?.nickname?.trim()"
                  class="mr-2 text-gray-400"
                >
                  {{ selectedContact }}
                </span>
                <template v-if="isSearchMode">
                  搜尋結果：{{ displayMessages.length }} 則（關鍵字「{{ activeSearchKeyword }}」）
                </template>
                <template v-else>
                  共 {{ displayMessages.length }} 則訊息
                </template>
              </p>
            </div>

            <div class="flex shrink-0 items-center gap-3">
              <div class="flex items-center gap-2">
                <span class="text-xs text-gray-600">允許 AI 代答</span>
                <button
                  type="button"
                  role="switch"
                  :aria-checked="selectedContactAiEnabled"
                  :disabled="togglingContactAi"
                  class="relative inline-flex h-6 w-10 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-green-500 focus:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-50"
                  :class="selectedContactAiEnabled ? 'bg-green-500' : 'bg-gray-300'"
                  @click="handleContactAiToggle"
                >
                  <span
                    class="pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out"
                    :class="selectedContactAiEnabled ? 'translate-x-4' : 'translate-x-0'"
                  />
                </button>
              </div>
              <button
                type="button"
                class="rounded-lg bg-green-600 px-4 py-1.5 text-sm font-medium text-white transition hover:bg-green-700 disabled:cursor-not-allowed disabled:opacity-50"
                :disabled="refreshing || messagesLoading"
                @click="handleRefresh"
              >
                {{ refreshing ? '重新整理中...' : '重新整理' }}
              </button>
            </div>
          </div>

          <div class="mt-3 flex flex-wrap items-center gap-2">
            <label class="text-xs text-gray-600" for="assign-profile">綁定 AI 人格</label>
            <select
              id="assign-profile"
              v-model="selectedAssignProfileId"
              class="min-w-[180px] rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:cursor-not-allowed disabled:bg-gray-100"
              :disabled="assigningProfile || systemPrompts.length === 0"
              @change="handleAssignProfileChange"
            >
              <option value="">使用全域預設人設</option>
              <option
                v-for="prompt in systemPrompts"
                :key="prompt.id"
                :value="String(prompt.id)"
              >
                {{ prompt.name }}{{ prompt.is_active ? '（全域啟用）' : '' }}
              </option>
            </select>
            <span v-if="assigningProfile" class="text-xs text-gray-500">更新中...</span>
          </div>

          <div class="mt-3 rounded-lg border border-amber-200 bg-amber-50/60 p-3">
            <div class="mb-2 flex items-center justify-between gap-2">
              <label class="text-xs font-medium text-amber-900" for="contact-memory">
                長期記憶摘要
              </label>
              <span class="text-xs text-amber-700">
                每 20 則對話自動萃取 · 可手動微調
              </span>
            </div>
            <textarea
              id="contact-memory"
              v-model="memoryInput"
              rows="3"
              placeholder="尚無長期記憶。AI 會在對話累積後自動總結，您也可手動編輯。"
              class="w-full resize-y rounded-lg border border-amber-200 bg-white px-3 py-2 text-sm text-gray-900 placeholder-gray-400 focus:border-amber-400 focus:outline-none focus:ring-1 focus:ring-amber-400 disabled:cursor-not-allowed disabled:bg-gray-100"
              :disabled="savingMemory"
            />
            <div class="mt-2 flex items-center justify-end gap-2">
              <span v-if="savingMemory" class="text-xs text-gray-500">儲存中...</span>
              <button
                type="button"
                class="rounded-lg bg-amber-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-50"
                :disabled="savingMemory || !selectedContact"
                @click="handleSaveMemory"
              >
                儲存記憶
              </button>
            </div>
          </div>

          <!-- 搜尋列 -->
          <div class="mt-3 flex items-center gap-2">
            <div class="relative flex-1">
              <svg
                class="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
                />
              </svg>
              <input
                v-model="searchKeyword"
                type="text"
                placeholder="搜尋聊天紀錄..."
                class="w-full rounded-lg border border-gray-300 bg-white py-2 pl-9 pr-3 text-sm text-gray-900 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:cursor-not-allowed disabled:bg-gray-100"
                :disabled="!selectedContact || searching"
                @keydown="handleSearchKeydown"
              />
            </div>
            <button
              type="button"
              class="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
              :disabled="!selectedContact || !searchKeyword.trim() || searching"
              @click="handleSearch"
            >
              {{ searching ? '搜尋中...' : '搜尋' }}
            </button>
            <button
              type="button"
              class="rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
              :disabled="!selectedContact || (!isSearchMode && !searchKeyword.trim())"
              @click="handleClearSearch"
            >
              清除
            </button>
          </div>
        </div>

        <!-- 訊息區 -->
        <div
          ref="chatContainer"
          class="flex-1 overflow-y-auto p-5"
        >
          <div
            v-if="!selectedContact"
            class="flex h-full items-center justify-center text-gray-400"
          >
            請選擇左側聯絡人以查看對話
          </div>

          <div
            v-else-if="messagesLoading"
            class="flex h-full items-center justify-center text-gray-500"
          >
            載入對話中...
          </div>

          <div
            v-else-if="displayMessages.length === 0"
            class="flex h-full items-center justify-center text-gray-400"
          >
            {{ emptyMessageText }}
          </div>

          <div v-else class="space-y-4">
            <div
              v-for="msg in displayMessages"
              :key="msg.id"
              class="flex"
              :class="msg.is_ai ? 'justify-end' : 'justify-start'"
            >
              <div class="max-w-[70%]">
                <div
                  class="rounded-lg px-3 py-2 text-sm leading-relaxed shadow-sm"
                  :class="[
                    msg.is_ai
                      ? 'rounded-tr-none bg-[#95ec69] text-gray-900'
                      : 'rounded-tl-none bg-white text-gray-900',
                    messageHasMedia(msg.content) ? 'space-y-2' : '',
                  ]"
                >
                  <template
                    v-for="(segment, segIndex) in parseMessageSegments(msg.content)"
                    :key="`${msg.id}-seg-${segIndex}`"
                  >
                    <img
                      v-if="segment.type === 'image'"
                      :src="segment.url"
                      alt="圖片訊息"
                      class="max-w-xs cursor-zoom-in rounded-lg shadow-sm transition hover:opacity-90"
                      loading="lazy"
                      @click="openImagePreview(segment.url)"
                    />
                    <audio
                      v-else-if="segment.type === 'voice'"
                      :src="segment.url"
                      controls
                      preload="metadata"
                      class="w-full min-w-[220px] max-w-xs"
                    />
                    <template v-else>
                      <template
                        v-for="(part, index) in getHighlightParts(segment.text, activeSearchKeyword)"
                        :key="`${msg.id}-${segIndex}-${index}`"
                      >
                        <mark
                          v-if="part.highlight"
                          class="rounded bg-yellow-200 px-0.5 font-semibold text-gray-900"
                        >{{ part.text }}</mark>
                        <template v-else>{{ part.text }}</template>
                      </template>
                    </template>
                  </template>
                </div>

                <p
                  class="mt-1 text-xs text-gray-400"
                  :class="msg.is_ai ? 'text-right' : 'text-left'"
                >
                  {{ formatTime(msg.created_at) }}
                </p>
              </div>
            </div>
          </div>
        </div>

        <!-- 底部輸入區 -->
        <div
          v-if="selectedContact"
          class="shrink-0 border-t border-gray-200 bg-white px-5 py-3"
        >
          <div
            v-if="selectedContactAiEnabled && aiEnabled"
            class="rounded-lg bg-gray-50 px-4 py-3 text-center text-sm text-gray-500"
          >
            此聯絡人在 AI 名單中，訊息將由 AI 自動代答
          </div>

          <div
            v-else-if="selectedContactAiEnabled && !aiEnabled"
            class="rounded-lg bg-amber-50 px-4 py-3 text-center text-sm text-amber-700"
          >
            此聯絡人在 AI 名單，但全域 AI 已關閉，不會自動回覆
          </div>

          <div v-else class="space-y-2">
            <div class="flex items-center gap-2">
              <label class="text-xs text-gray-600" for="reply-photo">附加圖片</label>
              <select
                id="reply-photo"
                v-model="selectedReplyPhotoId"
                class="flex-1 rounded-lg border border-gray-300 px-2 py-1.5 text-sm text-gray-900 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                :disabled="sending || loadingPhotos || pastedImageFile"
                @change="handleReplyPhotoSelect"
              >
                <option :value="null">不附圖</option>
                <option
                  v-for="photo in replyPhotoOptions"
                  :key="photo.id"
                  :value="photo.id"
                >
                  {{ photo.name }}（{{ photo.keywords }}）
                </option>
              </select>
              <button
                type="button"
                class="text-xs text-blue-600 hover:underline"
                @click="openPhotoModal"
              >
                管理圖片庫
              </button>
            </div>

            <div
              v-if="pastedImageFile"
              class="flex items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 p-2"
            >
              <img
                :src="pastedImagePreviewUrl"
                alt="貼上截圖預覽"
                class="h-20 max-w-[120px] rounded object-contain"
              />
              <div class="min-w-0 flex-1">
                <p class="text-sm font-medium text-blue-900">已貼上截圖</p>
                <p class="text-xs text-blue-700">按發送即可傳送（Shift+Win+S 後 Ctrl+V）</p>
              </div>
              <button
                type="button"
                class="shrink-0 text-xs text-blue-700 hover:underline"
                :disabled="sending"
                @click="clearPastedImage"
              >
                移除
              </button>
            </div>

            <div
              v-else-if="selectedReplyPhotoId"
              class="flex items-center gap-3 rounded-lg border border-gray-200 bg-gray-50 p-2"
            >
              <img
                :src="getPhotoFileUrl(selectedReplyPhotoId)"
                alt="預覽圖片"
                class="h-16 w-16 rounded object-cover"
              />
              <p class="text-xs text-gray-500">發送時將一併傳送此圖片</p>
            </div>

            <div class="flex items-end gap-3">
              <textarea
                v-model="replyText"
                rows="2"
                placeholder="請輸入訊息；在框內 Ctrl+V 可貼上截圖（Shift+Win+S）..."
                class="flex-1 resize-none rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                :disabled="sending"
                @keydown="handleReplyKeydown"
                @paste="handleReplyPaste"
              />
              <button
                type="button"
                class="shrink-0 rounded-lg bg-blue-600 px-5 py-2 text-sm font-medium text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
                :disabled="!canManualReply"
                @click="handleSendReply"
              >
                {{ sending ? '發送中...' : '發送' }}
              </button>
            </div>
          </div>
        </div>
      </main>
    </div>

    <!-- 人設設定 Modal -->
    <div
      v-if="showPromptModal"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      @click.self="closePromptModal"
    >
      <div
        class="flex max-h-[90vh] w-full max-w-5xl flex-col rounded-xl bg-white shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="prompt-modal-title"
      >
        <div class="flex items-center justify-between border-b border-gray-200 px-6 py-4">
          <div>
            <h2 id="prompt-modal-title" class="text-lg font-semibold text-gray-900">
              ⚙️ 人設庫管理
            </h2>
            <p class="mt-0.5 text-xs text-gray-500">
              人設會儲存至資料庫，切換後立即套用至下一次 AI 回覆
            </p>
          </div>
          <button
            type="button"
            class="rounded-lg p-1.5 text-gray-400 transition hover:bg-gray-100 hover:text-gray-600"
            :disabled="savingSystemPrompt"
            @click="closePromptModal"
          >
            <span class="sr-only">關閉</span>
            <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div class="flex min-h-0 flex-1 flex-col md:flex-row">
          <!-- 左側人設列表 -->
          <aside class="w-full shrink-0 border-b border-gray-200 md:w-64 md:border-b-0 md:border-r">
            <div class="flex items-center justify-between px-4 py-3">
              <p class="text-sm font-medium text-gray-700">已儲存人設</p>
              <button
                type="button"
                class="rounded-md bg-gray-100 px-2 py-1 text-xs font-medium text-gray-700 transition hover:bg-gray-200 disabled:opacity-50"
                :disabled="loadingSystemPrompt || savingSystemPrompt"
                @click="startNewPrompt"
              >
                + 新增
              </button>
            </div>

            <div v-if="loadingSystemPrompt" class="px-4 py-8 text-center text-sm text-gray-500">
              載入中...
            </div>
            <ul v-else class="max-h-72 overflow-y-auto px-2 pb-3 md:max-h-none md:flex-1">
              <li
                v-for="prompt in systemPrompts"
                :key="prompt.id"
              >
                <button
                  type="button"
                  class="mb-1 flex w-full items-start justify-between rounded-lg px-3 py-2 text-left text-sm transition"
                  :class="
                    selectedPromptId === prompt.id
                      ? 'bg-blue-50 text-blue-900 ring-1 ring-blue-200'
                      : 'text-gray-700 hover:bg-gray-50'
                  "
                  :disabled="savingSystemPrompt"
                  @click="selectPromptById(prompt.id)"
                >
                  <span class="min-w-0 flex-1">
                    <span class="block truncate font-medium">{{ prompt.name }}</span>
                    <span class="mt-0.5 block truncate text-xs text-gray-500">
                      {{ prompt.content.slice(0, 36) }}{{ prompt.content.length > 36 ? '...' : '' }}
                    </span>
                  </span>
                  <span
                    v-if="prompt.is_active"
                    class="ml-2 shrink-0 rounded-full bg-green-100 px-2 py-0.5 text-[10px] font-semibold text-green-700"
                  >
                    使用中
                  </span>
                </button>
              </li>
              <li
                v-if="isNewPrompt"
                class="mb-1 rounded-lg bg-amber-50 px-3 py-2 text-sm font-medium text-amber-800 ring-1 ring-amber-200"
              >
                新建人設（未儲存）
              </li>
            </ul>
          </aside>

          <!-- 右側編輯區 -->
          <div class="flex min-h-0 flex-1 flex-col">
            <div class="flex-1 overflow-y-auto px-6 py-4">
              <label class="mb-2 block text-sm font-medium text-gray-700" for="prompt-name">
                人設名稱
              </label>
              <input
                id="prompt-name"
                v-model="promptName"
                type="text"
                maxlength="128"
                placeholder="例如：熱情女孩、專業助理..."
                class="mb-4 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                :disabled="loadingSystemPrompt || savingSystemPrompt"
              />

              <label class="mb-2 block text-sm font-medium text-gray-700" for="prompt-content">
                System Prompt 內容
              </label>
              <textarea
                id="prompt-content"
                v-model="systemPromptText"
                rows="12"
                placeholder="輸入 AI 人設與行為指引..."
                class="w-full resize-y rounded-lg border border-gray-300 px-4 py-3 text-sm leading-relaxed text-gray-900 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                :disabled="loadingSystemPrompt || savingSystemPrompt"
              />
            </div>

            <div class="flex flex-wrap items-center justify-between gap-3 border-t border-gray-200 px-6 py-4">
              <div class="min-h-[1.25rem]">
                <p
                  v-if="promptToast"
                  class="text-sm"
                  :class="promptToast.includes('失敗') ? 'text-red-600' : 'text-green-600'"
                >
                  {{ promptToast.includes('失敗') ? '❌ ' : '✅ ' }}{{ promptToast }}
                </p>
                <p v-else class="text-xs text-gray-400">
                  {{ systemPromptText.length }} 字
                  <span v-if="selectedPromptIsActive" class="ml-2 text-green-600">· 目前使用中</span>
                </p>
              </div>

              <div class="flex flex-wrap gap-2">
                <button
                  v-if="!isNewPrompt"
                  type="button"
                  class="rounded-lg border border-red-200 px-3 py-2 text-sm font-medium text-red-600 transition hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
                  :disabled="loadingSystemPrompt || savingSystemPrompt || systemPrompts.length <= 1"
                  @click="handleDeleteSystemPrompt"
                >
                  刪除
                </button>
                <button
                  type="button"
                  class="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
                  :disabled="savingSystemPrompt"
                  @click="closePromptModal"
                >
                  取消
                </button>
                <button
                  type="button"
                  class="rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 text-sm font-medium text-blue-700 transition hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-50"
                  :disabled="loadingSystemPrompt || savingSystemPrompt || !promptName.trim() || !systemPromptText.trim()"
                  @click="handleSaveSystemPrompt"
                >
                  {{ savingSystemPrompt ? '處理中...' : isNewPrompt ? '建立人設' : '儲存修改' }}
                </button>
                <button
                  v-if="!isNewPrompt"
                  type="button"
                  class="rounded-lg bg-green-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-green-700 disabled:cursor-not-allowed disabled:opacity-50"
                  :disabled="loadingSystemPrompt || savingSystemPrompt || selectedPromptIsActive || !promptName.trim() || !systemPromptText.trim()"
                  @click="handleActivateSystemPrompt"
                >
                  套用此人設
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 圖片庫 Modal -->
    <div
      v-if="showPhotoModal"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      @click.self="closePhotoModal"
    >
      <div class="flex max-h-[90vh] w-full max-w-4xl flex-col rounded-xl bg-white shadow-xl">
        <div class="flex items-center justify-between border-b border-gray-200 px-6 py-4">
          <div>
            <h2 class="text-lg font-semibold text-gray-900">📷 圖片庫管理</h2>
            <p class="mt-0.5 text-xs text-gray-500">
              上傳圖片並設定關鍵字與 prompt 說明，AI 可透過 [發送照片: 關鍵字] 自動發圖
            </p>
          </div>
          <button
            type="button"
            class="rounded-lg p-1.5 text-gray-400 transition hover:bg-gray-100 hover:text-gray-600"
            @click="closePhotoModal"
          >
            ✕
          </button>
        </div>

        <div class="grid flex-1 gap-6 overflow-y-auto px-6 py-4 md:grid-cols-2">
          <div>
            <h3 class="mb-3 text-sm font-medium text-gray-800">上傳新圖片</h3>
            <div class="space-y-3 rounded-lg border border-gray-200 bg-gray-50 p-4">
              <input
                type="file"
                accept="image/*"
                class="block w-full text-sm text-gray-600"
                :disabled="savingPhoto"
                @change="onPhotoFileChange"
              />
              <input
                v-model="photoUploadName"
                type="text"
                placeholder="顯示名稱（例如：窗外自拍）"
                class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
                :disabled="savingPhoto"
              />
              <input
                v-model="photoUploadKeywords"
                type="text"
                placeholder="關鍵字，逗號分隔（例如：自拍,自拍照）"
                class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
                :disabled="savingPhoto"
              />
              <textarea
                v-model="photoUploadHint"
                rows="3"
                placeholder="給 AI 的說明（例如：陽光很好的側臉自拍，適合回覆想看照片時）"
                class="w-full resize-y rounded-lg border border-gray-300 px-3 py-2 text-sm"
                :disabled="savingPhoto"
              />
              <div>
                <label class="mb-1 block text-xs text-gray-600" for="photo-upload-profile">
                  所屬 AI 人格
                </label>
                <select
                  id="photo-upload-profile"
                  v-model="photoUploadProfileId"
                  class="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900"
                  :disabled="savingPhoto || systemPrompts.length === 0"
                >
                  <option
                    v-for="prompt in systemPrompts"
                    :key="prompt.id"
                    :value="prompt.id"
                  >
                    {{ prompt.name }}{{ prompt.is_active ? '（全域啟用）' : '' }}
                  </option>
                </select>
              </div>
              <button
                type="button"
                class="w-full rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                :disabled="savingPhoto || !photoUploadFile || !photoUploadName.trim() || !photoUploadKeywords.trim() || photoUploadProfileId == null"
                @click="handleUploadPhoto"
              >
                {{ savingPhoto ? '上傳中...' : '上傳至圖片庫' }}
              </button>
            </div>
          </div>

          <div>
            <h3 class="mb-3 text-sm font-medium text-gray-800">
              已儲存圖片（{{ photos.length }}）
            </h3>
            <div v-if="loadingPhotos" class="py-8 text-center text-sm text-gray-500">
              載入中...
            </div>
            <ul v-else class="max-h-96 space-y-3 overflow-y-auto">
              <li
                v-for="photo in photos"
                :key="photo.id"
                class="flex gap-3 rounded-lg border border-gray-200 p-3"
              >
                <img
                  :src="getPhotoFileUrl(photo.id)"
                  :alt="photo.name"
                  class="h-16 w-16 shrink-0 rounded object-cover"
                />
                <div class="min-w-0 flex-1">
                  <p class="font-medium text-gray-900">{{ photo.name }}</p>
                  <p class="text-xs text-gray-500">關鍵字：{{ photo.keywords }}</p>
                  <p class="text-xs text-indigo-600">
                    所屬人格：{{ photo.profile_name || '未綁定' }}
                  </p>
                  <p class="mt-1 line-clamp-2 text-xs text-gray-600">
                    {{ photo.prompt_hint || '（無 prompt 說明）' }}
                  </p>
                  <p class="mt-1 text-[10px] text-gray-400">
                    AI 標籤：[發送照片: {{ photo.keywords.split(/[,，]/)[0].trim() }}]
                  </p>
                </div>
                <button
                  type="button"
                  class="shrink-0 self-start text-xs text-red-600 hover:underline"
                  :disabled="savingPhoto"
                  @click="handleDeletePhoto(photo.id, photo.name)"
                >
                  刪除
                </button>
              </li>
              <li v-if="photos.length === 0" class="py-8 text-center text-sm text-gray-500">
                尚無圖片，請先上傳
              </li>
            </ul>
          </div>
        </div>

        <div class="border-t border-gray-200 px-6 py-3">
          <p
            v-if="photoToast"
            class="text-sm"
            :class="photoToast.includes('失敗') ? 'text-red-600' : 'text-green-600'"
          >
            {{ photoToast }}
          </p>
        </div>
      </div>
    </div>

    <!-- 系統設定 Modal -->
    <div
      v-if="showSystemModal"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      @click.self="closeSystemModal"
    >
      <div class="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl bg-white shadow-xl">
        <div class="flex items-center justify-between border-b border-gray-200 px-6 py-4">
          <div>
            <h2 class="text-lg font-semibold text-gray-900">🛠️ 系統設定</h2>
            <p class="text-xs text-gray-500">微信帳號、Webhook 與 AI 大模型設定</p>
          </div>
          <button
            type="button"
            class="rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
            :disabled="generatingLoginQr || savingLlmConfig"
            @click="closeSystemModal"
          >
            關閉
          </button>
        </div>

        <div class="space-y-5 overflow-y-auto px-6 py-5">
          <section class="rounded-lg border border-indigo-200 bg-indigo-50/40 p-4">
            <h3 class="text-sm font-semibold text-indigo-900">🤖 AI 大模型設定</h3>
            <p class="mt-1 text-xs text-indigo-800">
              切換啟用中的模型後，下一次 AI 回覆與記憶萃取即會套用新設定。
            </p>

            <div v-if="loadingLlmConfigs" class="mt-3 text-sm text-gray-500">載入中...</div>
            <div v-else class="mt-3 space-y-2">
              <div
                v-for="config in llmConfigs"
                :key="config.id"
                class="rounded-lg border bg-white p-3 text-sm"
                :class="config.is_active ? 'border-indigo-400 ring-1 ring-indigo-200' : 'border-gray-200'"
              >
                <div class="flex items-start justify-between gap-2">
                  <div class="min-w-0 flex-1">
                    <p class="font-medium text-gray-900">
                      {{ config.name }}
                      <span
                        v-if="config.is_active"
                        class="ml-1 rounded-full bg-indigo-100 px-2 py-0.5 text-[10px] font-semibold text-indigo-700"
                      >
                        使用中
                      </span>
                    </p>
                    <p class="mt-0.5 text-xs text-gray-500">
                      {{ config.provider }} · {{ config.model_name }}
                    </p>
                    <p class="truncate text-xs text-gray-400">{{ config.base_url }}</p>
                    <p class="text-xs text-gray-400">temperature: {{ config.temperature }}</p>
                  </div>
                  <div class="flex shrink-0 flex-col gap-1">
                    <button
                      v-if="!config.is_active"
                      type="button"
                      class="rounded border border-indigo-300 bg-white px-2 py-1 text-xs text-indigo-700 hover:bg-indigo-50 disabled:opacity-50"
                      :disabled="savingLlmConfig"
                      @click="handleActivateLlmConfig(config.id)"
                    >
                      啟用
                    </button>
                    <button
                      type="button"
                      class="rounded border border-red-200 bg-white px-2 py-1 text-xs text-red-600 hover:bg-red-50 disabled:opacity-50"
                      :disabled="savingLlmConfig || config.is_active"
                      @click="handleDeleteLlmConfig(config)"
                    >
                      刪除
                    </button>
                  </div>
                </div>
              </div>
              <p v-if="llmConfigs.length === 0" class="text-sm text-gray-500">尚無 LLM 設定</p>
            </div>

            <div class="mt-4 rounded-lg border border-indigo-100 bg-white p-3">
              <p class="text-xs font-semibold text-gray-700">新增設定</p>
              <div class="mt-2 grid gap-2 sm:grid-cols-2">
                <label class="block text-xs text-gray-600 sm:col-span-2">
                  設定名稱
                  <input
                    v-model="llmForm.name"
                    type="text"
                    class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
                    placeholder="例如：本地 LM Studio"
                    :disabled="savingLlmConfig"
                  />
                </label>
                <label class="block text-xs text-gray-600">
                  供應商範本
                  <select
                    v-model="llmForm.provider"
                    class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
                    :disabled="savingLlmConfig"
                    @change="handleLlmProviderChange"
                  >
                    <option v-for="(_, key) in LLM_PROVIDER_PRESETS" :key="key" :value="key">
                      {{ key }}
                    </option>
                  </select>
                </label>
                <label class="block text-xs text-gray-600">
                  Temperature
                  <input
                    v-model.number="llmForm.temperature"
                    type="number"
                    min="0"
                    max="2"
                    step="0.1"
                    class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
                    :disabled="savingLlmConfig"
                  />
                </label>
                <label class="block text-xs text-gray-600 sm:col-span-2">
                  Base URL
                  <input
                    v-model="llmForm.base_url"
                    type="text"
                    class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
                    placeholder="https://api.openai.com/v1"
                    :disabled="savingLlmConfig"
                  />
                </label>
                <label class="block text-xs text-gray-600 sm:col-span-2">
                  Model Name
                  <input
                    v-model="llmForm.model_name"
                    type="text"
                    class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
                    placeholder="gpt-4o / deepseek-chat / qwen..."
                    :disabled="savingLlmConfig"
                  />
                </label>
                <label class="block text-xs text-gray-600 sm:col-span-2">
                  API Key（Ollama 等本地模型可留空）
                  <input
                    v-model="llmForm.api_key"
                    type="password"
                    class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
                    placeholder="sk-..."
                    :disabled="savingLlmConfig"
                  />
                </label>
              </div>
              <button
                type="button"
                class="mt-3 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
                :disabled="savingLlmConfig"
                @click="handleCreateLlmConfig"
              >
                {{ savingLlmConfig ? '處理中...' : '新增 LLM 設定' }}
              </button>
            </div>
          </section>

          <section class="rounded-lg border border-gray-200 bg-gray-50 p-4">
            <h3 class="text-sm font-semibold text-gray-800">目前綁定帳號</h3>
            <div v-if="loadingWechatAccount" class="mt-2 text-sm text-gray-500">載入中...</div>
            <div v-else-if="wechatAccount" class="mt-2 space-y-1 text-sm text-gray-700">
              <p>
                <span class="text-gray-500">wxid：</span>
                {{ wechatAccount.wx_id }}
              </p>
              <p v-if="wechatAccount.nickname">
                <span class="text-gray-500">暱稱：</span>
                {{ wechatAccount.nickname }}
              </p>
              <p>
                <span class="text-gray-500">Webhook：</span>
                {{ wechatAccount.webhook_registered ? '已註冊' : '未註冊' }}
              </p>
              <p class="text-xs text-gray-400">
                資料來源：{{ wechatAccount.source === 'database' ? '資料庫' : '環境變數' }}
              </p>
              <button
                type="button"
                class="mt-3 rounded-lg border border-red-200 bg-white px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
                :disabled="loggingOutWechat || generatingLoginQr"
                @click="handleLogoutWechat"
              >
                {{ loggingOutWechat ? '登出中...' : '登出微信帳號' }}
              </button>
            </div>
          </section>

          <section class="rounded-lg border border-green-200 bg-green-50/50 p-4">
            <h3 class="text-sm font-semibold text-green-900">新增 / 切換微信帳號</h3>
            <p class="mt-1 text-xs text-green-800">
              若要換帳號，請先點「登出微信帳號」，再產生 QR Code 掃碼。登入成功後會自動完成初始化與 Webhook 註冊。
            </p>
            <button
              type="button"
              class="mt-3 rounded-lg bg-green-600 px-4 py-2 text-sm font-medium text-white hover:bg-green-700 disabled:cursor-not-allowed disabled:opacity-50"
              :disabled="generatingLoginQr"
              @click="handleGenerateLoginQrcode"
            >
              {{ generatingLoginQr ? '產生中...' : '產生 QR Code 掃碼登入' }}
            </button>

            <div v-if="loginQrBase64" class="mt-4 flex flex-col items-center gap-3">
              <img
                :src="loginQrBase64"
                alt="微信登入 QR Code"
                class="h-56 w-56 rounded-lg border border-gray-200 bg-white object-contain p-2"
              />
              <p
                class="text-center text-sm"
                :class="{
                  'text-gray-600': loginStatusKey === 'pending' || loginStatusKey === 'scanned',
                  'text-amber-700': loginStatusKey === 'slider_confirm',
                  'text-green-700': loginStatusKey === 'success',
                  'text-red-600': loginStatusKey === 'error' || loginStatusKey === 'expired',
                }"
              >
                {{ loginStatusMessage || '等待掃碼...' }}
              </p>
              <p class="text-xs text-gray-400">每 3 秒自動檢查登入狀態</p>
            </div>
          </section>
        </div>

        <div class="border-t border-gray-200 px-6 py-3">
          <p
            v-if="systemToast"
            class="text-sm"
            :class="systemToast.includes('失敗') || systemToast.includes('過期') ? 'text-red-600' : 'text-green-600'"
          >
            {{ systemToast }}
          </p>
        </div>
      </div>
    </div>

    <!-- 操作手冊彈出視窗 -->
    <div
      v-if="showManualModal"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-3 sm:p-6"
      @click.self="closeManual"
    >
      <div
        class="flex h-[min(92vh,920px)] w-full max-w-5xl flex-col overflow-hidden rounded-xl bg-white shadow-2xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="manual-modal-title"
      >
        <div class="flex shrink-0 items-center justify-between border-b border-gray-200 bg-gray-50 px-4 py-3">
          <div class="flex min-w-0 flex-1 items-center gap-4">
            <div>
              <h2 id="manual-modal-title" class="text-base font-semibold text-gray-900">
                說明文件
              </h2>
              <p class="text-xs text-gray-500">{{ APP_VERSION }}</p>
            </div>
            <div class="flex rounded-lg border border-gray-200 bg-white p-0.5 text-xs">
              <button
                type="button"
                class="rounded-md px-3 py-1.5 font-medium transition"
                :class="manualTab === 'user' ? 'bg-indigo-600 text-white' : 'text-gray-600 hover:bg-gray-50'"
                @click="manualTab = 'user'"
              >
                一般使用
              </button>
              <button
                type="button"
                class="rounded-md px-3 py-1.5 font-medium transition"
                :class="manualTab === 'tech' ? 'bg-indigo-600 text-white' : 'text-gray-600 hover:bg-gray-50'"
                @click="manualTab = 'tech'"
              >
                技術手冊
              </button>
            </div>
          </div>
          <button
            type="button"
            class="shrink-0 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 transition hover:bg-gray-100"
            @click="closeManual"
          >
            關閉
          </button>
        </div>
        <iframe
          :key="manualTab"
          :src="manualEmbedUrl"
          class="min-h-0 flex-1 w-full border-0 bg-[#f4f6f8]"
          :title="manualTab === 'user' ? 'WeChat AI Bot 使用指南' : 'WeChat AI Bot 技術手冊'"
        />
      </div>
    </div>

    <!-- 編輯聯絡人暱稱 Modal -->
    <div
      v-if="showNicknameModal"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      @click.self="closeNicknameModal"
    >
      <div class="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
        <h2 class="text-lg font-semibold text-gray-900">✏️ 編輯聯絡人名稱</h2>
        <p class="mt-1 text-xs text-gray-500">
          原始 ID：{{ selectedContact }}
        </p>
        <input
          v-model="nicknameInput"
          type="text"
          maxlength="128"
          placeholder="輸入備註名稱，留空則恢復顯示 wx_id"
          class="mt-4 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          :disabled="savingNickname"
          @keydown.enter="handleSaveNickname"
        />
        <div class="mt-5 flex justify-end gap-2">
          <button
            type="button"
            class="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            :disabled="savingNickname"
            @click="closeNicknameModal"
          >
            取消
          </button>
          <button
            type="button"
            class="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            :disabled="savingNickname"
            @click="handleSaveNickname"
          >
            {{ savingNickname ? '儲存中...' : '儲存' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 圖片放大預覽 -->
    <div
      v-if="imagePreviewUrl"
      class="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 p-4"
      @click.self="closeImagePreview"
    >
      <button
        type="button"
        class="absolute right-4 top-4 rounded-lg bg-black/50 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-black/70"
        @click="closeImagePreview"
      >
        關閉
      </button>
      <img
        :src="imagePreviewUrl"
        alt="圖片預覽"
        class="max-h-[90vh] max-w-[95vw] rounded-lg object-contain shadow-2xl"
        @click.stop
      />
    </div>
  </div>
</template>
