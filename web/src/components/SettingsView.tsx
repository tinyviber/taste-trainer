import { useEffect, useMemo, useState } from 'react'
import { apiFetch, isUnauthorized, providerFetch } from '../api'
import type { ModelInfo, Provider, UserSettings } from '../types'

function enabledModels(provider: Provider | undefined): ModelInfo[] {
  if (!provider) return []
  const enabled = new Set(provider.selectedModelIds)
  return provider.models.filter(model => enabled.has(model.id))
}

export function SettingsView({ onUnauthorized }: { onUnauthorized?: () => void }) {
  const [providers, setProviders] = useState<Provider[]>([])
  const [providerId, setProviderId] = useState('')
  const [modelId, setModelId] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const provider = providers.find(item => item.id === providerId)
  const models = useMemo(() => enabledModels(provider), [provider])

  useEffect(() => {
    let cancelled = false
    void Promise.all([
      apiFetch<UserSettings>('/api/settings'),
      providerFetch<{ providers: Provider[] }>('/providers'),
    ]).then(([settings, data]) => {
      if (cancelled) return
      setProviders(data.providers)
      const savedProvider = data.providers.find(item => item.id === settings.default_provider_id)
      const savedModels = enabledModels(savedProvider)
      setProviderId(savedProvider?.id ?? '')
      setModelId(savedModels.some(model => model.id === settings.default_model_id)
        ? settings.default_model_id
        : savedModels[0]?.id ?? '')
    }).catch(caught => {
      if (cancelled) return
      if (isUnauthorized(caught)) onUnauthorized?.()
      else setError(caught instanceof Error ? caught.message : '设置加载失败')
    }).finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => { cancelled = true }
  }, [onUnauthorized])

  const selectProvider = (nextId: string) => {
    const nextProvider = providers.find(item => item.id === nextId)
    const nextModels = enabledModels(nextProvider)
    setProviderId(nextId)
    setModelId(nextModels.some(model => model.id === modelId) ? modelId : nextModels[0]?.id ?? '')
    setNotice('')
  }

  const save = async () => {
    setSaving(true); setError(''); setNotice('')
    try {
      await apiFetch<UserSettings>('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ default_provider_id: providerId, default_model_id: modelId }),
      })
      setNotice('Default model 已保存，之后的新题、评分和分析会使用它。')
    } catch (caught) {
      if (isUnauthorized(caught)) onUnauthorized?.()
      else setError(caught instanceof Error ? caught.message : '设置保存失败')
    } finally {
      setSaving(false)
    }
  }

  return <>
    <div className="page-head settings-head">
      <div><span className="eyebrow">Settings / model routing</span><h1>Choose your<br /><em>default model.</em></h1></div>
    </div>
    <section className="panel settings-panel">
      <div className="section-title"><div><span className="eyebrow">Training model</span><h2>Default model</h2></div><span className="count-label">PER USER</span></div>
      <p className="settings-intro">从 Providers 中已勾选的模型里选择一个。API key 仍只保存在 provider-service，Settings 只保存路由选择。</p>
      {error && <p className="form-message form-message-error" role="alert">{error}</p>}
      {loading ? <div className="loading-state">加载设置…</div> : !providers.length ? <div className="empty settings-empty"><strong>还没有 provider</strong><span>先在 Providers 配置并保存一个 provider。</span></div> : <div className="settings-form">
        <label className="settings-field">Default provider<select value={providerId} onChange={event => selectProvider(event.target.value)}>
          <option value="">选择 provider</option>
          {providers.map(item => <option key={item.id} value={item.id}>{item.name || item.id}</option>)}
        </select></label>
        <label className="settings-field">Default model<select value={modelId} onChange={event => { setModelId(event.target.value); setNotice('') }} disabled={!providerId || !models.length}>
          <option value="">{providerId && !models.length ? '该 provider 没有已勾选模型' : '选择 model'}</option>
          {models.map(model => <option key={model.id} value={model.id}>{model.id}{model.owned_by ? ` · ${model.owned_by}` : ''}</option>)}
        </select></label>
        {providerId && !models.length && <p className="settings-help">回到 Providers 检测模型并勾选至少一个模型，再回来设置 Default model。</p>}
        {notice && <p className="form-message form-message-success" role="status">✓ {notice}</p>}
        <div className="settings-actions"><button className="button button-primary" type="button" disabled={saving || !providerId || !modelId} onClick={() => void save()}>{saving ? '保存中…' : '保存设置'}</button></div>
      </div>}
    </section>
  </>
}
