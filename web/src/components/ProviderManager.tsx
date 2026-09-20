import { useCallback, useEffect, useState } from 'react'
import { jsonBody, providerFetch } from '../api'
import type { ModelInfo, Provider } from '../types'

function Button({ children, variant = 'secondary', ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'danger' }) {
  return <button className={`button button-${variant}`} {...props}>{children}</button>
}

type Draft = Provider & { apiKey: string; notice: string; busy: boolean; error: string }

function toDraft(provider: Provider): Draft {
  return { ...provider, apiKey: '', notice: '', busy: false, error: '' }
}

const blankProvider = (): Draft => ({ id: `draft-${crypto.randomUUID()}`, name: '', baseUrl: 'https://api.openai.com/v1', hasApiKey: false, models: [], selectedModelIds: [], apiKey: '', notice: '', busy: false, error: '' })

export function ProviderManager({ onCountChange }: { onCountChange: (count: number) => void }) {
  const [providers, setProviders] = useState<Draft[]>([])
  const [loading, setLoading] = useState(true)
  const [globalError, setGlobalError] = useState('')

  const load = useCallback(async () => {
    try {
      const data = await providerFetch<{ providers: Provider[] }>('/providers')
      setProviders(data.providers.map(toDraft)); onCountChange(data.providers.length)
    } catch (caught) { setGlobalError(caught instanceof Error ? caught.message : '加载 provider 失败') }
    finally { setLoading(false) }
  }, [onCountChange])

  useEffect(() => { void load() }, [load])

  const update = (id: string, patch: Partial<Draft>) => setProviders(current => current.map(provider => provider.id === id ? { ...provider, ...patch } : provider))
  const save = async (draft: Draft) => {
    const isDraft = draft.id.startsWith('draft-')
    update(draft.id, { busy: true, error: '', notice: '' })
    try {
      const data = await providerFetch<{ provider: Provider }>('/providers', jsonBody({ id: isDraft ? undefined : draft.id, name: draft.name, baseUrl: draft.baseUrl, apiKey: draft.apiKey || undefined, selectedModelIds: draft.selectedModelIds }))
      let next = { ...toDraft(data.provider), apiKey: '' }
      if (data.provider.hasApiKey) {
        try {
          const detected = await providerFetch<{ provider: Provider; models: ModelInfo[] }>(`/providers/${encodeURIComponent(data.provider.id)}/discover`, { method: 'POST' })
          next = { ...toDraft(detected.provider), apiKey: '', notice: `已自动检测到 ${detected.models.length} 个模型` }
        } catch (caught) {
          next = { ...next, notice: '配置已保存，但自动检测失败', error: caught instanceof Error ? caught.message : '模型检测失败' }
        }
      }
      setProviders(current => current.map(item => item.id === draft.id ? next : item))
      if (isDraft) onCountChange(providers.filter(item => !item.id.startsWith('draft-')).length + 1)
    } catch (caught) { update(draft.id, { error: caught instanceof Error ? caught.message : '保存失败' }) }
  }
  const discover = async (draft: Draft) => {
    update(draft.id, { busy: true, error: '', notice: '正在检测 /models…' })
    try {
      const data = await providerFetch<{ provider: Provider; models: ModelInfo[] }>(`/providers/${encodeURIComponent(draft.id)}/discover`, { method: 'POST' })
      setProviders(current => current.map(item => item.id === draft.id ? { ...item, ...data.provider, apiKey: '', busy: false, notice: `检测到 ${data.models.length} 个模型` } : item))
    } catch (caught) { update(draft.id, { busy: false, error: caught instanceof Error ? caught.message : '模型检测失败' }) }
  }
  const remove = async (draft: Draft) => {
    if (!window.confirm(`删除 provider「${draft.name || draft.id}」？`)) return
    if (draft.id.startsWith('draft-')) { setProviders(current => current.filter(item => item.id !== draft.id)); return }
    try { await providerFetch(`/providers/${encodeURIComponent(draft.id)}`, { method: 'DELETE' }); setProviders(current => current.filter(item => item.id !== draft.id)); onCountChange(Math.max(0, providers.length - 1)) }
    catch (caught) { update(draft.id, { error: caught instanceof Error ? caught.message : '删除失败' }) }
  }
  const add = () => setProviders(current => [...current, blankProvider()])

  return <><div className="page-head provider-head"><div><span className="eyebrow">Configuration / model access</span><h1>Providers<br /><em>your models, your route.</em></h1></div><Button variant="primary" onClick={add}>添加 provider <span>＋</span></Button></div>{globalError && <div className="alert">{globalError}</div>}<div className="provider-intro">把 API key 和兼容接口的 base URL 放在服务端配置中；前端只拿到掩码和模型列表。检测成功后勾选允许 Taste Trainer 使用的模型。</div>{loading ? <div className="loading-state">加载 providers…</div> : providers.length ? <div className="provider-grid">{providers.map((draft, index) => <ProviderCard key={draft.id || `new-${index}`} draft={draft} update={patch => update(draft.id, patch)} onSave={() => void save(draft)} onDiscover={() => void discover(draft)} onDelete={() => void remove(draft)} />)}</div> : <div className="empty provider-empty"><strong>还没有 provider</strong><span>添加一个 OpenAI-compatible endpoint，开始发现模型。</span><Button variant="primary" onClick={add}>添加第一个 provider</Button></div>}</>
}

function ProviderCard({ draft, update, onSave, onDiscover, onDelete }: { draft: Draft; update: (patch: Partial<Draft>) => void; onSave: () => void; onDiscover: () => void; onDelete: () => void }) {
  const selected = new Set(draft.selectedModelIds)
  const allSelected = draft.models.length > 0 && draft.models.every(model => selected.has(model.id))
  const setAll = (value: boolean) => update({ selectedModelIds: value ? draft.models.map(model => model.id) : [], notice: '' })
  const toggle = (id: string) => update({ selectedModelIds: selected.has(id) ? draft.selectedModelIds.filter(item => item !== id) : [...draft.selectedModelIds, id], notice: '' })
  return <article className="provider-card"><div className="provider-card-head"><div><span className="provider-index">PROVIDER</span><h2>{draft.name || 'New provider'}</h2></div>{draft.id && <button className="icon-button" aria-label="删除 provider" onClick={onDelete}>×</button>}</div><label>Provider name<input value={draft.name} onChange={event => update({ name: event.target.value })} placeholder="OpenRouter / LM Studio / My API" /></label><label>API key<div className="secret-input"><input type="password" value={draft.apiKey} onChange={event => update({ apiKey: event.target.value })} placeholder={draft.hasApiKey ? '已保存，留空表示不修改' : 'sk-…'} /><span>{draft.hasApiKey ? 'SAVED' : 'NEW'}</span></div></label><label>Custom base URL<input value={draft.baseUrl} onChange={event => update({ baseUrl: event.target.value })} placeholder="https://api.example.com/v1" /></label><div className="provider-actions"><Button variant="primary" disabled={draft.busy || !draft.name || !draft.baseUrl} onClick={onSave}>保存并检测</Button><Button disabled={draft.busy || !draft.id || draft.id.startsWith('draft-')} onClick={onDiscover}>重新检测 <span>↗</span></Button></div>{draft.notice && <p className="success-note">✓ {draft.notice}</p>}{draft.error && <p className="error-note">{draft.error}</p>}<div className="models-head"><div><span className="eyebrow">Available models</span><strong>{draft.models.length ? `${draft.selectedModelIds.length} / ${draft.models.length} selected` : '保存后自动检测'}</strong></div><div className="select-actions"><button onClick={() => setAll(true)} disabled={!draft.models.length}>全部</button><button onClick={() => setAll(false)} disabled={!draft.models.length}>取消全部</button></div></div>{draft.models.length > 0 ? <div className="model-list">{draft.models.map(model => <label className="model-row" key={model.id}><input type="checkbox" checked={selected.has(model.id)} onChange={() => toggle(model.id)} /><span className="checkmark">✓</span><span className="model-name">{model.id}</span>{model.owned_by && <small>{model.owned_by}</small>}</label>)}</div> : <div className="model-empty">模型列表将在服务端调用<br /><code>{draft.baseUrl.replace(/\/$/, '')}/models</code> 后出现。</div>}<p className="provider-footnote">Selected models become <code>{draft.id || 'provider'}:model-id</code> in the AI SDK registry.</p></article>
}
