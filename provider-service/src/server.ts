import { createServer, type IncomingMessage, type ServerResponse } from 'node:http'
import { chmod, mkdir, readFile, rename, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { randomUUID } from 'node:crypto'
import { generateText } from 'ai'
import { createConfiguredRegistry, type ProviderConfig } from './providers.js'

type ModelInfo = { id: string; owned_by?: string }
type StoredProvider = ProviderConfig & {
  models: ModelInfo[]
  selectionInitialized: boolean
  lastDiscoveryAt?: string
}

const port = Number(process.env.PROVIDER_PORT ?? 8765)
const allowedOrigin = process.env.WEB_ORIGIN ?? 'http://localhost:5173'
const apiToken = process.env.API_TOKEN ?? ''
const workspace = process.env.WORKSPACE_DIR ?? process.cwd()
const configPath = resolve(process.env.PROVIDER_CONFIG_PATH ?? `${workspace}/.a2h/providers.json`)
let writeQueue = Promise.resolve()

function sendJson(response: ServerResponse, status: number, value: unknown) {
  response.statusCode = status
  response.setHeader('Content-Type', 'application/json; charset=utf-8')
  response.end(JSON.stringify(value))
}

function configureCors(response: ServerResponse) {
  response.setHeader('Access-Control-Allow-Origin', allowedOrigin)
  response.setHeader('Access-Control-Allow-Credentials', 'true')
  response.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-Token')
  response.setHeader('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
}

function authorized(request: IncomingMessage) {
  if (!apiToken) return true
  const header = request.headers['x-token']
  if (header === apiToken) return true
  const cookies = request.headers.cookie ?? ''
  return cookies.split(';').some(cookie => cookie.trim() === `trainer_token=${apiToken}`)
}

async function body(request: IncomingMessage) {
  const chunks: Buffer[] = []
  for await (const chunk of request) chunks.push(Buffer.from(chunk))
  if (!chunks.length) return {}
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')) as Record<string, unknown> }
  catch { throw new Error('请求 JSON 无效') }
}

async function readProviders(): Promise<StoredProvider[]> {
  try {
    const raw = JSON.parse(await readFile(configPath, 'utf8'))
    return Array.isArray(raw) ? raw : []
  } catch (error: unknown) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return []
    throw error
  }
}

async function saveProviders(providers: StoredProvider[]) {
  await mkdir(dirname(configPath), { recursive: true, mode: 0o700 })
  const tempPath = `${configPath}.${process.pid}.tmp`
  await writeFile(tempPath, `${JSON.stringify(providers, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 })
  await chmod(tempPath, 0o600)
  await rename(tempPath, configPath)
}

function queueSave(providers: StoredProvider[]) {
  writeQueue = writeQueue.then(() => saveProviders(providers))
  return writeQueue
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
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error('base URL 必须使用 http 或 https')
  url.pathname = url.pathname.replace(/\/(chat\/completions|completions)\/?$/, '')
  return url.toString().replace(/\/$/, '')
}

function modelsUrl(baseUrl: string) {
  return `${baseUrl.replace(/\/$/, '')}/models`
}

async function discover(provider: StoredProvider) {
  if (!provider.apiKey) throw new Error('请先填写 API key 并保存')
  const response = await fetch(modelsUrl(provider.baseUrl), {
    headers: { Accept: 'application/json', Authorization: `Bearer ${provider.apiKey}` },
    signal: AbortSignal.timeout(15000),
  })
  const data = await response.json().catch(() => null) as { data?: unknown } | null
  if (!response.ok) throw new Error(`模型检测失败（${response.status}）：${JSON.stringify(data).slice(0, 300)}`)
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

async function route(request: IncomingMessage, response: ServerResponse) {
  const url = new URL(request.url ?? '/', 'http://localhost')
  const parts = url.pathname.split('/').filter(Boolean)
  if (request.method === 'GET' && url.pathname === '/health') return sendJson(response, 200, { ok: true })
  if (parts[0] !== 'providers') return sendJson(response, 404, { error: 'not found' })
  const providers = await readProviders()

  if (request.method === 'GET' && parts.length === 1) return sendJson(response, 200, { providers: providers.map(publicProvider) })
  if (request.method === 'POST' && parts.length === 1) {
    const input = await body(request)
    const existing = typeof input.id === 'string' && input.id ? providers.find(item => item.id === input.id) : undefined
    const id = existing?.id ?? randomUUID()
    const provider: StoredProvider = {
      id,
      name: requireText(input.name, 'provider name'),
      baseUrl: normalizeBaseUrl(requireText(input.baseUrl, 'base URL')),
      apiKey: typeof input.apiKey === 'string' && input.apiKey.trim() ? input.apiKey.trim() : existing?.apiKey ?? '',
      models: existing?.models ?? [],
      selectedModelIds: Array.isArray(input.selectedModelIds) ? [...new Set(input.selectedModelIds.filter(item => typeof item === 'string'))] : existing?.selectedModelIds ?? [],
      selectionInitialized: Array.isArray(input.selectedModelIds) || (existing?.selectionInitialized ?? false),
      lastDiscoveryAt: existing?.lastDiscoveryAt,
    }
    const next = existing ? providers.map(item => item.id === id ? provider : item) : [...providers, provider]
    await queueSave(next)
    return sendJson(response, 200, { provider: publicProvider(provider) })
  }

  const provider = findProvider(providers, parts[1] ?? '')
  if (request.method === 'DELETE' && parts.length === 2) {
    await queueSave(providers.filter(item => item.id !== provider.id))
    return sendJson(response, 200, { ok: true })
  }
  if (request.method === 'POST' && parts[2] === 'discover') {
    const models = await discover(provider)
    const known = new Set(models.map(model => model.id))
    const selected = provider.selectionInitialized ? provider.selectedModelIds.filter(id => known.has(id)) : models.map(model => model.id)
    const nextProvider = { ...provider, models, selectedModelIds: selected, selectionInitialized: true, lastDiscoveryAt: new Date().toISOString() }
    await queueSave(providers.map(item => item.id === provider.id ? nextProvider : item))
    return sendJson(response, 200, { provider: publicProvider(nextProvider), models })
  }
  if (request.method === 'POST' && parts[2] === 'chat') {
    const input = await body(request)
    const modelId = requireText(input.modelId, 'modelId')
    const prompt = requireText(input.prompt, 'prompt')
    if (!provider.selectedModelIds.includes(modelId)) throw new Error('该模型未被勾选启用')
    const registry = createConfiguredRegistry([provider])
    const result = await generateText({ model: registry.languageModel(`${provider.id}:${modelId}`), prompt })
    return sendJson(response, 200, { text: result.text, usage: result.usage })
  }
  return sendJson(response, 404, { error: 'not found' })
}

const server = createServer(async (request, response) => {
  configureCors(response)
  if (request.method === 'OPTIONS') return response.end()
  if (!authorized(request)) return sendJson(response, 401, { error: 'bad token' })
  try { await route(request, response) }
  catch (error: unknown) { sendJson(response, 400, { error: error instanceof Error ? error.message : '请求失败' }) }
})

server.listen(port, '127.0.0.1', () => console.log(`provider service listening on 127.0.0.1:${port}`))
