export async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...init, credentials: 'include' })
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
