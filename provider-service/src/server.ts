import { createServer, request as httpRequest, type IncomingMessage, type ServerResponse } from 'node:http'
import { request as httpsRequest } from 'node:https'
import { lookup } from 'node:dns/promises'
import { chmod, mkdir, readFile, readdir, rename, rm, stat, writeFile } from 'node:fs/promises'
import { createCipheriv, createDecipheriv, createHash, createHmac, randomBytes, timingSafeEqual } from 'node:crypto'
import { isIP } from 'node:net'
import { dirname, join, resolve } from 'node:path'
import { generateText } from 'ai'
import { createConfiguredRegistry, type ProviderConfig } from './providers.js'

type ModelInfo = { id: string; owned_by?: string }
type StoredProvider = ProviderConfig & {
  models: ModelInfo[]
  selectionInitialized: boolean
  lastDiscoveryAt?: string
}
type EncryptedStore = { version: 1; userId: string; iv: string; tag: string; ciphertext: string }

const port = Number(process.env.PROVIDER_PORT ?? 8765)
const internalSecret = process.env.PROVIDER_INTERNAL_SECRET ?? ''
if (internalSecret.length < 32 || internalSecret.startsWith('replace-with')) {
  throw new Error('PROVIDER_INTERNAL_SECRET must be a random secret of at least 32 characters')
}
const providerDataDir = resolve(process.env.PROVIDER_DATA_DIR ?? './provider-data')
const nonceDir = resolve(process.env.PROVIDER_NONCE_DIR ?? join(providerDataDir, '.nonces'))
const legacyConfigPath = process.env.PROVIDER_LEGACY_CONFIG_PATH ? resolve(process.env.PROVIDER_LEGACY_CONFIG_PATH) : ''
const legacyUserId = process.env.PROVIDER_MIGRATION_USER_ID ?? ''
const deleteLegacy = process.env.PROVIDER_DELETE_LEGACY === 'true'
const encryptionKey = readEncryptionKey()
const writeQueues = new Map<string, Promise<void>>()
const INTERNAL_WINDOW_MS = 60_000
const MAX_BODY_BYTES = 1_000_000

function readEncryptionKey() {
  const encoded = process.env.PROVIDER_ENCRYPTION_KEY ?? ''
  const key = Buffer.from(encoded, 'base64')
  if (key.length !== 32) {
    throw new Error('PROVIDER_ENCRYPTION_KEY must be a base64-encoded 32-byte key')
  }
  return key
}

function sendJson(response: ServerResponse, status: number, value: unknown) {
  response.statusCode = status
  response.setHeader('Content-Type', 'application/json; charset=utf-8')
  response.end(JSON.stringify(value))
}

function safeUserId(userId: string) {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(userId)) {
    throw new Error('invalid authenticated user')
  }
  return userId
}

function providerPath(userId: string) {
  return join(providerDataDir, `${safeUserId(userId)}.json.enc`)
}

function aad(userId: string) {
  return Buffer.from(`provider-store:v1:${safeUserId(userId)}`)
}

function encryptStore(userId: string, providers: StoredProvider[], key = encryptionKey): EncryptedStore {
  const iv = randomBytes(12)
  const cipher = createCipheriv('aes-256-gcm', key, iv)
  cipher.setAAD(aad(userId))
  const ciphertext = Buffer.concat([cipher.update(JSON.stringify(providers), 'utf8'), cipher.final()])
  return {
    version: 1,
    userId,
    iv: iv.toString('base64'),
    tag: cipher.getAuthTag().toString('base64'),
    ciphertext: ciphertext.toString('base64'),
  }
}

function decryptStore(userId: string, store: EncryptedStore, key = encryptionKey): StoredProvider[] {
  if (store.version !== 1 || store.userId !== userId) throw new Error('provider store is invalid')
  const decipher = createDecipheriv('aes-256-gcm', key, Buffer.from(store.iv, 'base64'))
  decipher.setAAD(aad(userId))
  decipher.setAuthTag(Buffer.from(store.tag, 'base64'))
  const plaintext = Buffer.concat([
    decipher.update(Buffer.from(store.ciphertext, 'base64')),
    decipher.final(),
  ]).toString('utf8')
  const parsed = JSON.parse(plaintext) as unknown
  if (!Array.isArray(parsed)) throw new Error('provider store is invalid')
  return parsed as StoredProvider[]
}

async function readProviders(userId: string): Promise<StoredProvider[]> {
  const path = providerPath(userId)
  try {
    const parsed = JSON.parse(await readFile(path, 'utf8')) as EncryptedStore
    return decryptStore(userId, parsed)
  } catch (error: unknown) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return []
    throw new Error('provider store cannot be read')
  }
}

async function saveProviders(userId: string, providers: StoredProvider[]) {
  const path = providerPath(userId)
  await mkdir(dirname(path), { recursive: true, mode: 0o700 })
  const tempPath = `${path}.${process.pid}.tmp`
  await writeFile(tempPath, `${JSON.stringify(encryptStore(userId, providers))}\n`, { encoding: 'utf8', mode: 0o600 })
  await chmod(tempPath, 0o600)
  await rename(tempPath, path)
  await chmod(path, 0o600)
}

async function rotateEncryptedStores(oldKeyText: string) {
  const oldKey = Buffer.from(oldKeyText, 'base64')
  if (oldKey.length !== 32) throw new Error('PROVIDER_ROTATE_FROM_KEY must be a base64-encoded 32-byte key')
  await mkdir(providerDataDir, { recursive: true, mode: 0o700 })
  for (const filename of await readdir(providerDataDir)) {
    if (!filename.endsWith('.json.enc')) continue
    const userId = filename.slice(0, -'.json.enc'.length)
    const path = join(providerDataDir, filename)
    const store = JSON.parse(await readFile(path, 'utf8')) as EncryptedStore
    const providers = decryptStore(userId, store, oldKey)
    const tempPath = `${path}.${process.pid}.rotate.tmp`
    await writeFile(tempPath, `${JSON.stringify(encryptStore(userId, providers))}\n`, { encoding: 'utf8', mode: 0o600 })
    await chmod(tempPath, 0o600)
    await rename(tempPath, path)
  }
}

function queueSave(userId: string, providers: StoredProvider[]) {
  const current = writeQueues.get(userId) ?? Promise.resolve()
  const next = current.then(() => saveProviders(userId, providers))
  writeQueues.set(userId, next.catch(() => undefined))
  return next
}

async function migrateLegacy() {
  if (!legacyConfigPath || !legacyUserId) return
  const target = providerPath(legacyUserId)
  try { await stat(target); return } catch (error: unknown) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
  }
  let providers: StoredProvider[]
  try {
    providers = JSON.parse(await readFile(legacyConfigPath, 'utf8')) as StoredProvider[]
    if (!Array.isArray(providers)) throw new Error('legacy provider file is invalid')
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return
    throw new Error('legacy provider migration failed')
  }
  if (!deleteLegacy) {
    throw new Error('plaintext legacy providers found; set PROVIDER_DELETE_LEGACY=true for one-shot encrypted migration')
  }
  await saveProviders(legacyUserId, providers)
  const migrated = await readProviders(legacyUserId)
  if (migrated.length !== providers.length) throw new Error('legacy provider migration verification failed')
  await rm(legacyConfigPath, { force: true })
  console.log(`migrated legacy providers for ${legacyUserId}`)
}

function publicProvider(provider: StoredProvider) {
  return {
    id: provider.id,
    name: provider.name,
    baseUrl: provider.baseUrl,
    hasApiKey: Boolean(provider.apiKey),
    models: provider.models ?? [],
    selectedModelIds: provider.selectedModelIds ?? [],
    lastDiscoveryAt: provider.lastDiscoveryAt,
  }
}

function requireText(value: unknown, label: string) {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`${label} 不能为空`)
  return value.trim()
}

function normalizeBaseUrl(value: string) {
  const url = new URL(value)
  if (url.protocol !== 'https:' && process.env.ALLOW_INSECURE_PROVIDER_HTTP !== 'true') {
    throw new Error('公网 provider 必须使用 HTTPS')
  }
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error('base URL 必须使用 http 或 https')
  url.username = ''
  url.password = ''
  url.pathname = url.pathname.replace(/\/(chat\/completions|completions)\/?$/, '')
  return url.toString().replace(/\/$/, '')
}

function isBlockedIp(address: string) {
  if (isIP(address) === 4) {
    const octets = address.split('.').map(Number)
    const value = (((octets[0] * 256 + octets[1]) * 256 + octets[2]) * 256 + octets[3]) >>> 0
    const inRange = (start: number, end: number) => value >= start && value <= end
    return inRange(0x00000000, 0x00ffffff) || inRange(0x0a000000, 0x0affffff) ||
      inRange(0x64400000, 0x647fffff) || inRange(0x7f000000, 0x7fffffff) ||
      inRange(0xa9fe0000, 0xa9feffff) || inRange(0xac100000, 0xac1fffff) ||
      inRange(0xc0000000, 0xc00000ff) || inRange(0xc0000200, 0xc00002ff) ||
      inRange(0xc0000800, 0xc00008ff) || inRange(0xc0120000, 0xc01200ff) ||
      inRange(0xc6120000, 0xc613ffff) || inRange(0xc6336400, 0xc63364ff) ||
      inRange(0xcb007100, 0xcb0071ff) || inRange(0xe0000000, 0xffffffff)
  }
  const normalized = address.toLowerCase()
  if (normalized.startsWith('::ffff:')) return isBlockedIp(normalized.slice('::ffff:'.length))
  return normalized === '::' || normalized === '::1' || normalized.startsWith('fc') ||
    normalized.startsWith('fd') || normalized.startsWith('fe8') || normalized.startsWith('fe9') ||
    normalized.startsWith('fea') || normalized.startsWith('feb') || normalized.startsWith('ff') ||
    normalized.startsWith('2001:db8:') || normalized.startsWith('2001:10:')
}

async function safeAddresses(value: string) {
  const url = new URL(value)
  if (process.env.ALLOW_PRIVATE_PROVIDER_HOSTS === 'true') return [url.hostname]
  if (url.hostname === 'metadata.google.internal' || url.hostname === 'instance-data') {
    throw new Error('provider host is not allowed')
  }
  const addresses = isIP(url.hostname) ? [url.hostname] : (await lookup(url.hostname, { all: true })).map(item => item.address)
  if (!addresses.length || addresses.some(isBlockedIp)) throw new Error('provider host is not allowed')
  return addresses
}

async function assertSafeUrl(value: string) {
  await safeAddresses(value)
}

function pinnedRequest(urlValue: string, address: string, init: RequestInit = {}) {
  const url = new URL(urlValue)
  const requestFunction = url.protocol === 'https:' ? httpsRequest : httpRequest
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  headers.delete('host')
  const payload = typeof init.body === 'string' ? Buffer.from(init.body) : undefined
  return new Promise<{ status: number; headers: Headers; body: Buffer }>((resolveRequest, rejectRequest) => {
    const request = requestFunction({
      protocol: url.protocol,
      hostname: address,
      port: url.port || undefined,
      path: `${url.pathname}${url.search}`,
      method,
      headers: { ...Object.fromEntries(headers.entries()), Host: url.host },
      ...(url.protocol === 'https:' ? { servername: url.hostname } : {}),
    }, response => {
      const chunks: Buffer[] = []
      let size = 0
      response.on('data', chunk => {
        const value = Buffer.from(chunk)
        size += value.length
        if (size > 16 * 1024 * 1024) {
          request.destroy(new Error('provider response too large'))
          return
        }
        chunks.push(value)
      })
      response.on('end', () => {
        const responseHeaders = new Headers()
        for (const [key, value] of Object.entries(response.headers)) {
          if (Array.isArray(value)) value.forEach(item => responseHeaders.append(key, item))
          else if (value !== undefined) responseHeaders.set(key, value)
        }
        resolveRequest({ status: response.statusCode ?? 502, headers: responseHeaders, body: Buffer.concat(chunks) })
      })
    })
    request.setTimeout(15_000, () => request.destroy(new Error('provider request timed out')))
    request.on('error', rejectRequest)
    if (payload) request.write(payload)
    request.end()
  })
}

async function safeFetch(input: Parameters<typeof fetch>[0], init?: Parameters<typeof fetch>[1]) {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
  const addresses = await safeAddresses(url)
  const address = addresses?.[0]
  if (!address) throw new Error('provider host is not allowed')
  const body = init?.body ?? (input instanceof Request ? await input.clone().text() : undefined)
  const bodyText = body === undefined || typeof body === 'string'
    ? body
    : await new Response(body as BodyInit).text()
  const headers = new Headers(input instanceof Request ? input.headers : undefined)
  new Headers(init?.headers).forEach((value, key) => headers.set(key, value))
  const requestInit = { ...(init ?? {}), headers, body: bodyText }
  const response = await pinnedRequest(url, address, requestInit)
  return new Response(response.body.toString('utf8'), { status: response.status, headers: response.headers })
}

function modelsUrl(baseUrl: string) {
  return `${baseUrl.replace(/\/$/, '')}/models`
}

async function discover(baseUrl: string, apiKey: string) {
  if (!apiKey) throw new Error('请先填写 API key')
  const response = await safeFetch(modelsUrl(baseUrl), {
    headers: { Accept: 'application/json', Authorization: `Bearer ${apiKey}` },
  })
  const data = await response.json().catch(() => null) as { data?: unknown } | null
  if (!response.ok) throw new Error(`模型检测失败（HTTP ${response.status}）`)
  const rows = Array.isArray(data) ? data : data?.data
  if (!Array.isArray(rows)) throw new Error('响应不是 OpenAI-compatible 模型列表')
  return rows.map(row => ({
    id: typeof row === 'string' ? row : String((row as Record<string, unknown>).id ?? ''),
    owned_by: typeof row === 'object' && row ? String((row as Record<string, unknown>).owned_by ?? '') || undefined : undefined,
  })).filter(model => model.id)
}

function findProvider(providers: StoredProvider[], id: string) {
  const provider = providers.find(item => item.id === id)
  if (!provider) throw new Error('provider 不存在')
  return provider
}

function parseBody(body: Buffer): Record<string, unknown> {
  if (!body.length) return {}
  try { return JSON.parse(body.toString('utf8')) as Record<string, unknown> }
  catch { throw new Error('请求 JSON 无效') }
}

async function route(request: IncomingMessage, response: ServerResponse, userId: string, body: Buffer) {
  const url = new URL(request.url ?? '/', 'http://localhost')
  const parts = url.pathname.split('/').filter(Boolean)
  if (request.method === 'GET' && parts.length === 1 && parts[0] === 'providers') {
    const providers = await readProviders(userId)
    return sendJson(response, 200, { providers: providers.map(publicProvider) })
  }
  if (request.method === 'POST' && parts.length === 2 && parts[0] === 'providers' && parts[1] === 'test') {
    const input = parseBody(body)
    const baseUrl = normalizeBaseUrl(requireText(input.baseUrl, 'base URL'))
    const models = await discover(baseUrl, requireText(input.apiKey, 'API key'))
    return sendJson(response, 200, { models })
  }
  const providers = await readProviders(userId)
  if (request.method === 'POST' && parts.length === 1 && parts[0] === 'providers') {
    const input = parseBody(body)
    const existing = typeof input.id === 'string' && input.id ? providers.find(item => item.id === input.id) : undefined
    const id = existing?.id ?? randomBytes(16).toString('hex')
    const baseUrl = normalizeBaseUrl(requireText(input.baseUrl, 'base URL'))
    const submittedKey = typeof input.apiKey === 'string' ? input.apiKey.trim() : ''
    if (existing && existing.baseUrl !== baseUrl && !submittedKey) {
      throw new Error('修改 base URL 后必须重新填写 API key')
    }
    await assertSafeUrl(baseUrl)
    const provider: StoredProvider = {
      id,
      name: requireText(input.name, 'provider name'),
      baseUrl,
      apiKey: submittedKey || existing?.apiKey || '',
      models: existing?.models ?? [],
      selectedModelIds: Array.isArray(input.selectedModelIds)
        ? [...new Set(input.selectedModelIds.filter(item => typeof item === 'string'))]
        : existing?.selectedModelIds ?? [],
      selectionInitialized: Array.isArray(input.selectedModelIds) || (existing?.selectionInitialized ?? false),
      lastDiscoveryAt: existing?.lastDiscoveryAt,
    }
    const next = existing ? providers.map(item => item.id === id ? provider : item) : [...providers, provider]
    await queueSave(userId, next)
    return sendJson(response, 200, { provider: publicProvider(provider) })
  }

  if (parts[0] !== 'providers') return sendJson(response, 404, { error: 'not found' })
  const provider = findProvider(providers, parts[1] ?? '')
  if (request.method === 'DELETE' && parts.length === 2) {
    await queueSave(userId, providers.filter(item => item.id !== provider.id))
    return sendJson(response, 200, { ok: true })
  }
  if (request.method === 'POST' && parts.length === 3 && parts[2] === 'discover') {
    const input = parseBody(body)
    const submittedBaseUrl = typeof input.baseUrl === 'string' && input.baseUrl.trim()
      ? normalizeBaseUrl(input.baseUrl) : provider.baseUrl
    const submittedKey = typeof input.apiKey === 'string' ? input.apiKey.trim() : ''
    if (submittedBaseUrl !== provider.baseUrl && !submittedKey) {
      throw new Error('修改 base URL 后必须重新填写 API key')
    }
    const apiKey = submittedKey || provider.apiKey
    const models = await discover(submittedBaseUrl, apiKey)
    const known = new Set(models.map(model => model.id))
    const selected = provider.selectionInitialized
      ? provider.selectedModelIds.filter(id => known.has(id)) : models.map(model => model.id)
    const nextProvider = {
      ...provider, baseUrl: submittedBaseUrl, apiKey, models,
      selectedModelIds: selected, selectionInitialized: true,
      lastDiscoveryAt: new Date().toISOString(),
    }
    await queueSave(userId, providers.map(item => item.id === provider.id ? nextProvider : item))
    return sendJson(response, 200, { provider: publicProvider(nextProvider), models })
  }
  if (request.method === 'POST' && parts.length === 3 && parts[2] === 'chat') {
    const input = parseBody(body)
    const modelId = requireText(input.modelId, 'modelId')
    const prompt = requireText(input.prompt, 'prompt')
    if (!provider.selectedModelIds.includes(modelId)) throw new Error('该模型未被勾选启用')
    await assertSafeUrl(provider.baseUrl)
    const registry = createConfiguredRegistry([provider], safeFetch)
    const result = await generateText({ model: registry.languageModel(`${provider.id}:${modelId}`), prompt })
    return sendJson(response, 200, { text: result.text, usage: result.usage })
  }
  return sendJson(response, 404, { error: 'not found' })
}

async function readBody(request: IncomingMessage) {
  const chunks: Buffer[] = []
  let size = 0
  for await (const chunk of request) {
    size += Buffer.byteLength(chunk)
    if (size > MAX_BODY_BYTES) throw new Error('请求体过大')
    chunks.push(Buffer.from(chunk))
  }
  return Buffer.concat(chunks)
}

async function claimNonce(nonce: string) {
  await mkdir(nonceDir, { recursive: true, mode: 0o700 })
  const marker = join(nonceDir, nonce)
  try {
    await mkdir(marker, { mode: 0o700 })
  } catch (error: unknown) {
    if ((error as NodeJS.ErrnoException).code === 'EEXIST') return false
    throw error
  }
  // Persistent nonce markers survive a provider process restart.  Cleanup is
  // best effort and never runs before the marker for this request is created.
  void (async () => {
    try {
      const now = Date.now()
      for (const entry of await readdir(nonceDir)) {
        const candidate = join(nonceDir, entry)
        const info = await stat(candidate)
        if (now - info.mtimeMs > 5 * 60_000) await rm(candidate, { recursive: true, force: true })
      }
    } catch { /* cleanup must not affect a valid request */ }
  })()
  return true
}

async function authorize(request: IncomingMessage, body: Buffer) {
  if (!internalSecret) throw new Error('internal authentication is not configured')
  const userId = request.headers['x-provider-user']
  const timestamp = request.headers['x-provider-timestamp']
  const nonce = request.headers['x-provider-nonce']
  const signature = request.headers['x-provider-signature']
  if (typeof userId !== 'string' || typeof timestamp !== 'string' || typeof nonce !== 'string' || typeof signature !== 'string') {
    return false
  }
  if (!/^\d{10}$/.test(timestamp) || !/^[a-f0-9]{32}$/i.test(nonce) || !/^[a-f0-9]{64}$/i.test(signature)) return false
  if (Math.abs(Date.now() - Number(timestamp) * 1000) > INTERNAL_WINDOW_MS) return false
  safeUserId(userId)
  const url = new URL(request.url ?? '/', 'http://localhost')
  const path = url.pathname + url.search
  const canonical = `${timestamp}\n${nonce}\n${(request.method ?? 'GET').toUpperCase()}\n${path}\n${createHash('sha256').update(body).digest('hex')}\n${userId}`
  const expected = createHmac('sha256', internalSecret).update(canonical).digest('hex')
  if (!timingSafeEqual(Buffer.from(signature), Buffer.from(expected))) return false
  return claimNonce(nonce)
}

function publicError(error: unknown) {
  const message = error instanceof Error ? error.message : ''
  if (/^(模型检测失败（HTTP \d+）|响应不是 OpenAI-compatible 模型列表|provider host is not allowed|公网 provider 必须使用 HTTPS|修改 base URL 后必须重新填写 API key|请先填写 API key|API key 不能为空|base URL 不能为空|provider name 不能为空|modelId 不能为空|prompt 不能为空|该模型未被勾选启用|provider 不存在|请求 JSON 无效|请求体过大)$/.test(message)) {
    return message
  }
  return 'provider 请求失败'
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url ?? '/', 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/health') return sendJson(response, 200, { ok: true })
  try {
    const body = await readBody(request)
    const userId = await authorize(request, body)
    if (!userId) return sendJson(response, 401, { error: 'unauthorized' })
    await route(request, response, request.headers['x-provider-user'] as string, body)
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : ''
    const status = message === '请求体过大' ? 413 : 400
    sendJson(response, status, { error: publicError(error) })
  }
})

await mkdir(providerDataDir, { recursive: true, mode: 0o700 })
await chmod(providerDataDir, 0o700)
if (process.env.PROVIDER_ROTATE_FROM_KEY) {
  await rotateEncryptedStores(process.env.PROVIDER_ROTATE_FROM_KEY)
  console.log('rotated encrypted provider stores')
  process.exit(0)
}
await migrateLegacy()
server.listen(port, '127.0.0.1', () => console.log(`provider service listening on 127.0.0.1:${port}`))
