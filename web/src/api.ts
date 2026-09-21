const bootstrapToken = readBootstrapToken()

function readBootstrapToken() {
  const url = new URL(window.location.href)
  const token = url.searchParams.get('token') ?? ''
  if (token) {
    url.searchParams.delete('token')
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`)
  }
  return token
}

function requestHeaders(init?: RequestInit) {
  const headers = new Headers(init?.headers)
  if (bootstrapToken && !headers.has('X-Token')) headers.set('X-Token', bootstrapToken)
  return headers
}

export async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...init, headers: requestHeaders(init), credentials: 'include' })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(data.detail ?? data.error ?? '请求失败')
  }
  return data as T
}

export async function providerFetch<T>(path: string, init?: RequestInit): Promise<T> {
  return apiFetch<T>(`${import.meta.env.VITE_PROVIDER_API_URL ?? '/provider-api'}${path}`, init)
}

export function jsonBody(body: unknown): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}
