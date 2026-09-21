let csrfToken = ''

function cookieValue(name: string) {
  const prefix = `${name}=`
  const entry = document.cookie.split('; ').find(value => value.startsWith(prefix))
  return entry ? decodeURIComponent(entry.slice(prefix.length)) : ''
}

export class ApiError extends Error {
  readonly status: number
  readonly data: unknown

  constructor(status: number, message: string, data: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.data = data
  }
}

export function setCsrfToken(token?: string | null) {
  csrfToken = token ?? ''
}

export function clearCsrfToken() {
  csrfToken = ''
}

export function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401
}

function requestHeaders(init?: RequestInit) {
  const headers = new Headers(init?.headers)
  const method = (init?.method ?? 'GET').toUpperCase()
  const token = csrfToken || cookieValue('trainer_csrf')
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && token && !headers.has('X-CSRF-Token')) {
    headers.set('X-CSRF-Token', token)
  }
  return headers
}

export async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...init, headers: requestHeaders(init), credentials: 'include' })
  const responseCsrfToken = response.headers.get('X-CSRF-Token')
  if (responseCsrfToken) setCsrfToken(responseCsrfToken)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = data && typeof data === 'object' && 'detail' in data ? data.detail : undefined
    const error = data && typeof data === 'object' && 'error' in data ? data.error : undefined
    const message = typeof detail === 'string' ? detail : typeof error === 'string' ? error : '请求失败'
    throw new ApiError(response.status, message, data)
  }
  return data as T
}

export async function providerFetch<T>(path: string, init?: RequestInit): Promise<T> {
  return apiFetch<T>(`${import.meta.env.VITE_PROVIDER_API_URL ?? '/api/provider-api'}${path}`, init)
}

export function jsonBody(body: unknown): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}
