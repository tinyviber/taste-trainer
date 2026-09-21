import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch, clearCsrfToken, isUnauthorized, jsonBody, providerFetch, setCsrfToken } from './api'
import type { AuthResponse, AuthUser, Exercise, Job, Principle, PrinciplesState, TrainerState, Video } from './types'
import { ProviderManager } from './components/ProviderManager'

type View = 'practice' | 'providers'

function statusLabel(status: string) {
  return ({
    prompted: '待提交',
    submitted: '已提交',
    needs_micro_revision: '待 micro-v2',
    reviewed: '已完成',
    succeeded: '完成',
    running: '处理中',
    failed: '失败',
  } as Record<string, string>)[status] ?? status
}

function StatusPill({ status, score }: { status: string; score?: number | null }) {
  return <span className={`pill pill-${status}`}>{statusLabel(status)}{score != null ? ` · ${score}/100` : ''}</span>
}

function SectionTitle({ eyebrow, title, action }: { eyebrow: string; title: string; action?: React.ReactNode }) {
  return <div className="section-title"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2></div>{action}</div>
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>
}

function A2HLink({ url }: { url?: string }) {
  if (!url) return null
  try {
    const target = new URL(url, window.location.href)
    const localHosts = new Set(['localhost', '127.0.0.1', '::1'])
    const pointsToLocalViewer = localHosts.has(target.hostname)
    const browserIsLocal = localHosts.has(window.location.hostname)
    if (pointsToLocalViewer && !browserIsLocal) {
      return <span className="muted small" title="服务器上的 A2H 仅监听本机；主流程内容已在此页面展示">A2H / optional</span>
    }
    return <a className="text-link" href={target.toString()} target="_blank" rel="noreferrer">Open A2H ↗</a>
  } catch {
    return null
  }
}

type DocumentCardProps = {
  eyebrow: string
  title: string
  content: string
  tone?: 'prompt' | 'review' | 'revision' | 'default'
}

function DocumentCard({ eyebrow, title, content, tone = 'default' }: DocumentCardProps) {
  const trimmed = content.trim()
  if (!trimmed) return null
  return <article className={`document-card document-card-${tone}`}>
    <div className="document-card-head"><span className="eyebrow">{eyebrow}</span><span className="muted small">完整内容</span></div>
    <h3>{title}</h3>
    <pre className="document-body">{trimmed}</pre>
  </article>
}

function ExerciseDocuments({ exercise, documents }: { exercise: Exercise; documents?: TrainerState['documents'] }) {
  if (!documents) return null
  return <section className="panel documents-panel">
    <SectionTitle eyebrow="Read in dashboard" title="题目、提交与反馈" action={<span className="count-label">A2H optional</span>} />
    <p className="document-intro">主流程需要的内容直接显示在这里；A2H 只负责工作区的深度浏览。</p>
    <div className="document-list">
      <DocumentCard eyebrow="Prompt / 题目" title={exercise.title} content={documents.prompt} tone="prompt" />
      {exercise.status !== 'prompted' && <DocumentCard eyebrow="Submission / 我的方案" title="你的分镜方案" content={documents.submission} />}
      <DocumentCard eyebrow="Review / 评分" title="评分与修改意见" content={documents.review} tone="review" />
      <DocumentCard eyebrow="micro-v2 / 局部改写" title="我的局部改写" content={documents.micro_revision} />
      <DocumentCard eyebrow="Revision / 修改版" title="完整修改版分镜" content={documents.revision} tone="revision" />
    </div>
  </section>
}

function Button({ children, variant = 'secondary', ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'danger' }) {
  return <button className={`button button-${variant}`} {...props}>{children}</button>
}

function PracticeView({ onUnauthorized }: { onUnauthorized?: () => void }) {
  const [state, setState] = useState<TrainerState | null>(null)
  const [principles, setPrinciples] = useState<PrinciplesState>({ pending: [], accepted: [] })
  const [submission, setSubmission] = useState('')
  const [micro, setMicro] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)
  const initializedPromptRef = useRef<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [nextState, nextPrinciples] = await Promise.all([
        apiFetch<TrainerState>('/api/state'),
        apiFetch<PrinciplesState>('/api/principles'),
      ])
      setState(nextState)
      setPrinciples(nextPrinciples)
      if (nextState.latest_exercise?.status === 'prompted' && nextState.latest_exercise.id !== initializedPromptRef.current) {
        setSubmission(nextState.submission_template)
        initializedPromptRef.current = nextState.latest_exercise.id
      }
    } catch (caught) {
      if (isUnauthorized(caught)) {
        onUnauthorized?.()
        return
      }
      setError(caught instanceof Error ? caught.message : '刷新失败')
    }
  }, [onUnauthorized])

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => void refresh(), 3000)
    return () => window.clearInterval(timer)
  }, [refresh])

  const latest = state?.latest_exercise
  const run = async (url: string, init?: RequestInit) => {
    setBusy(true); setError('')
    try { await apiFetch(url, init); await refresh() }
    catch (caught) { setError(caught instanceof Error ? caught.message : '操作失败') }
    finally { setBusy(false) }
  }

  const submit = () => latest && void run(`/api/exercise/${encodeURIComponent(latest.id)}/submit`, jsonBody({ text: submission }))
  const submitMicro = () => latest && void run(`/api/exercise/${encodeURIComponent(latest.id)}/micro-revise`, jsonBody({ text: micro }))
  const analyze = (video: Video) => void run('/api/analyze', jsonBody({ video: video.name }))
  const upload = async () => {
    const file = fileRef.current?.files?.[0]
    if (!file) return
    const form = new FormData(); form.append('file', file)
    await run('/api/analyze', { method: 'POST', body: form })
  }
  const actionPrinciple = (candidate: Principle, action: 'accept' | 'reject', title = candidate.title, detail = candidate.detail) =>
    void run(`/api/principles/${encodeURIComponent(candidate.id)}`, jsonBody({ action, title, detail }))

  return <>
    <header className="mobile-header"><span className="brand-mark">Taste Trainer</span><span className="mobile-subtitle">Video → palate</span></header>
    <div className="page-head">
      <div><span className="eyebrow">Practice / today</span><h1>Train your palate<br /><em>one video at a time.</em></h1></div>
      <div className="head-actions"><A2HLink url={state?.a2h_url} /><span className="sync-dot" /> <span className="muted">同步中</span></div>
    </div>

    {error && <div className="alert">{error}<button onClick={() => setError('')}>×</button></div>}

    <section className="hero-card">
      <div className="hero-top"><div><span className="eyebrow">Current exercise</span><h2>{latest?.title ?? '还没有练习'}</h2><p>{latest ? 'Watch, taste, and identify the key flavor notes.' : '点击下方按钮生成第一道练习题。'}</p></div><div className="exercise-count"><strong>{state?.exercises.length ?? 0}</strong><span>EXERCISES</span></div></div>
      {latest ? <div className="exercise-meta"><StatusPill status={latest.status} score={latest.score} /><span className="meta-divider" /><span className="muted mono">{latest.id}</span></div> : <div className="empty hero-empty">你的训练记录会从这里开始。</div>}
    </section>

    {latest && <ExerciseDocuments exercise={latest} documents={state?.documents} />}

    <div className="two-column">
      <section className="panel submission-panel">
        <SectionTitle eyebrow="Write it out" title={latest?.status === 'needs_micro_revision' ? '先完成 micro-v2' : '你的分镜方案'} />
        {latest?.status === 'prompted' ? <><textarea value={submission} onChange={event => setSubmission(event.target.value)} placeholder="00:00–00:05 画面……" /><Button variant="primary" disabled={busy || !submission.trim()} onClick={submit}>提交并打分 <span>→</span></Button></> : latest?.status === 'needs_micro_revision' ? <><div className="focus-note"><span className="eyebrow">Focus / {state?.micro_focus?.dim ?? latest.weakest ?? 'weakest dimension'}</span><p>{state?.micro_focus?.original ?? '请重写一个 5–10 秒片段。'}</p><small>{state?.micro_focus?.gap ?? ''}</small></div><textarea value={micro} onChange={event => setMicro(event.target.value)} placeholder="只重写上面指出的一个 5–10 秒片段" /><Button variant="primary" disabled={busy || !micro.trim()} onClick={submitMicro}>提交 micro-v2 <span>→</span></Button></> : <Empty>{latest ? '这道题已完成。点击“出新题”继续。' : '还没有可提交的练习。'}</Empty>}
      </section>

      <section className="panel action-panel">
        <SectionTitle eyebrow="Keep going" title="下一步" />
        <div className="action-stack"><Button variant="primary" disabled={busy} onClick={() => void run('/api/exercise/new?force=0', { method: 'POST' })}>出一道新题 <span>→</span></Button><Button disabled={busy} onClick={() => void run('/api/exercise/new?force=1', { method: 'POST' })}>跳过当前题重出</Button></div>
        <div className="rule" /><p className="muted small">建议完成一轮 micro-v2 后再开始下一题，这样反馈会进入下一次出题。</p>
      </section>
    </div>

    <section className="panel"><SectionTitle eyebrow="Video library" title="分析视频" action={<span className="count-label">{state?.videos.length ?? 0} clips</span>} />
      {state?.videos.length ? <div className="video-list">{state.videos.map(video => <div className="video-row" key={`${video.inbox}-${video.name}`}><div><span className="video-icon">{video.inbox ? '↓' : '▶'}</span><span>{video.name}</span>{video.inbox && <span className="tag">INBOX</span>}</div><Button disabled={busy} onClick={() => analyze(video)}>分析</Button></div>)}</div> : <Empty>videos/ 为空。上传一个视频开始分析。</Empty>}
      <div className="upload-row"><input ref={fileRef} type="file" accept="video/*" /><Button variant="secondary" disabled={busy} onClick={() => void upload()}>上传并分析</Button></div>
    </section>

    <div className="two-column lower-grid">
      <section className="panel"><SectionTitle eyebrow="Taste system" title="口味原则候选" /><p className="muted small">视频分析产生的原则不会自动进入评分基准，请人工接受或拒绝。</p>{principles.pending.length ? <div className="principle-list">{principles.pending.map(candidate => <PrincipleCard key={candidate.id} candidate={candidate} busy={busy} onAction={actionPrinciple} />)}</div> : <Empty>暂无待确认的原则候选。</Empty>}</section>
      <section className="panel"><SectionTitle eyebrow="Operations" title="任务日志" />{Object.keys(state?.jobs ?? {}).length ? <div className="job-list">{Object.entries(state?.jobs ?? {}).reverse().map(([id, job]) => <div className="job-row" key={id}><StatusPill status={job.status} /><span className="mono truncate">{id}</span><span className="muted truncate">{job.detail}</span></div>)}</div> : <Empty>还没有后台任务。</Empty>}</section>
    </div>

    <section className="panel history-panel"><SectionTitle eyebrow="History" title="最近练习" />{state?.exercises.length ? <div className="history-table"><div className="history-head"><span>TITLE</span><span>STATUS</span><span>SCORE</span></div>{[...(state.exercises ?? [])].reverse().map(exercise => <div className="history-row" key={exercise.id}><strong>{exercise.title || exercise.id}</strong><StatusPill status={exercise.status} /><span className="mono">{exercise.score != null ? `${exercise.score}/100` : '—'}</span></div>)}</div> : <Empty>完成第一道题后，这里会显示训练历史。</Empty>}</section>
  </>
}

function PrincipleCard({ candidate, busy, onAction }: { candidate: Principle; busy: boolean; onAction: (candidate: Principle, action: 'accept' | 'reject', title?: string, detail?: string) => void }) {
  const [title, setTitle] = useState(candidate.title)
  const [detail, setDetail] = useState(candidate.detail)
  return <div className="principle-card"><input value={title} onChange={event => setTitle(event.target.value)} /><textarea value={detail} onChange={event => setDetail(event.target.value)} /><small className="muted">来源：{candidate.source_video || '—'}</small><div className="inline-actions"><Button disabled={busy} onClick={() => onAction(candidate, 'accept', title, detail)}>修改后接受</Button><Button variant="ghost" disabled={busy} onClick={() => onAction(candidate, 'reject')}>拒绝</Button></div></div>
}

function authToken(data: AuthResponse): string | undefined {
  return data.csrfToken ?? data.csrf_token
}

function errorMessage(caught: unknown, fallback: string) {
  return caught instanceof Error ? caught.message : fallback
}

function LoginScreen({ busy, error, onSubmit }: { busy: boolean; error: string; onSubmit: (username: string, password: string) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  return <main className="auth-page">
    <section className="auth-card" aria-labelledby="login-title">
      <div className="auth-brand">Taste<br /><span>Trainer</span></div>
      <span className="eyebrow">Private practice space</span>
      <h1 id="login-title">Welcome back.</h1>
      <p className="auth-intro">登录后访问你的训练记录和 provider 配置。</p>
      <form className="auth-form" onSubmit={event => { event.preventDefault(); onSubmit(username.trim(), password) }}>
        <label className="auth-field">用户名<input value={username} onChange={event => setUsername(event.target.value)} autoComplete="username" required /></label>
        <label className="auth-field">密码<input type="password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="current-password" required /></label>
        {error && <p className="form-message form-message-error" role="alert">{error}</p>}
        <Button variant="primary" type="submit" disabled={busy || !username.trim() || !password}>{busy ? '登录中…' : '登录'}</Button>
      </form>
    </section>
  </main>
}

type AccountControlProps = {
  user: AuthUser
  onChangePassword: (currentPassword: string, newPassword: string) => Promise<void>
  onLogout: () => Promise<void>
}

function AccountControl({ user, onChangePassword, onLogout }: AccountControlProps) {
  const [open, setOpen] = useState(false)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [logoutBusy, setLogoutBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const changePassword = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError(''); setNotice('')
    if (newPassword.length < 12) {
      setError('新密码至少需要 12 个字符。')
      return
    }
    if (newPassword !== confirmPassword) {
      setError('两次输入的新密码不一致。')
      return
    }
    setBusy(true)
    try {
      await onChangePassword(currentPassword, newPassword)
      setCurrentPassword(''); setNewPassword(''); setConfirmPassword('')
      setNotice('密码已更新。')
    } catch (caught) {
      setError(errorMessage(caught, '修改密码失败'))
    } finally {
      setBusy(false)
    }
  }

  const logout = async () => {
    setLogoutBusy(true); setError('')
    try { await onLogout() } catch (caught) { setError(errorMessage(caught, '退出登录失败')) }
    finally { setLogoutBusy(false) }
  }

  return <div className="account-control">
    <button className="account-trigger" type="button" aria-expanded={open} onClick={() => { setOpen(value => !value); setError('') }}>
      <span className="account-avatar">{user.username.slice(0, 1).toUpperCase()}</span>
      <span className="account-trigger-copy"><small>Signed in as</small><strong>{user.username}</strong></span>
      <span className="account-chevron">{open ? '⌃' : '⌄'}</span>
    </button>
    {open && <div className="account-panel" role="dialog" aria-label="账户设置">
      <div className="account-panel-head"><div><span className="eyebrow">Account</span><h2>{user.username}</h2></div><button className="icon-button" type="button" aria-label="关闭账户面板" onClick={() => setOpen(false)}>×</button></div>
      <form className="account-form" onSubmit={changePassword}>
        <p className="muted small">修改密码后，其他已登录会话会失效。</p>
        <label className="auth-field">当前密码<input type="password" value={currentPassword} onChange={event => setCurrentPassword(event.target.value)} autoComplete="current-password" required /></label>
        <label className="auth-field">新密码<input type="password" value={newPassword} onChange={event => setNewPassword(event.target.value)} autoComplete="new-password" minLength={12} required /></label>
        <label className="auth-field">确认新密码<input type="password" value={confirmPassword} onChange={event => setConfirmPassword(event.target.value)} autoComplete="new-password" minLength={12} required /></label>
        {error && <p className="form-message form-message-error" role="alert">{error}</p>}
        {notice && <p className="form-message form-message-success" role="status">{notice}</p>}
        <div className="account-actions"><Button variant="primary" type="submit" disabled={busy || logoutBusy}>{busy ? '保存中…' : '修改密码'}</Button><Button variant="ghost" type="button" disabled={busy || logoutBusy} onClick={() => void logout()}>{logoutBusy ? '退出中…' : '退出登录'}</Button></div>
      </form>
    </div>}
  </div>
}

function AuthenticatedShell({ user, onUnauthorized, onChangePassword, onLogout }: { user: AuthUser; onUnauthorized: () => void; onChangePassword: AccountControlProps['onChangePassword']; onLogout: AccountControlProps['onLogout'] }) {
  const [view, setView] = useState<View>(() => window.location.hash === '#providers' ? 'providers' : 'practice')
  const [providerCount, setProviderCount] = useState(0)
  useEffect(() => {
    void providerFetch<{ providers: unknown[] }>('/providers').then(data => setProviderCount(data.providers.length)).catch(caught => {
      if (isUnauthorized(caught)) onUnauthorized()
    })
  }, [onUnauthorized, view])
  const navigate = (next: View) => { window.history.replaceState(null, '', next === 'providers' ? '#providers' : '#practice'); setView(next) }
  return <div className="app-shell"><aside className="sidebar"><div className="brand">Taste<br /><span>Trainer</span></div><p className="tagline">TRAIN YOUR PALATE<br />ONE VIDEO AT A TIME.</p><nav><button className={view === 'practice' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('practice')}><span>◉</span>Practice</button><button className="nav-item" onClick={() => navigate('practice')}><span>▱</span>Library</button><button className={view === 'providers' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('providers')}><span>◌</span>Providers {providerCount > 0 && <b>{providerCount}</b>}</button></nav><div className="sidebar-account"><AccountControl user={user} onChangePassword={onChangePassword} onLogout={onLogout} /></div><div className="sidebar-foot">BETTER COOKS<br />TASTE MORE.</div></aside><main className="main-content"><div className="mobile-account-control"><AccountControl user={user} onChangePassword={onChangePassword} onLogout={onLogout} /></div>{view === 'practice' ? <PracticeView onUnauthorized={onUnauthorized} /> : <ProviderManager onCountChange={setProviderCount} onUnauthorized={onUnauthorized} />}</main></div>
}

export default function App() {
  const [authState, setAuthState] = useState<'loading' | 'authenticated' | 'unauthenticated'>('loading')
  const [user, setUser] = useState<AuthUser | null>(null)
  const [authBusy, setAuthBusy] = useState(false)
  const [authError, setAuthError] = useState('')

  const expireSession = useCallback((message = '登录已过期，请重新登录。') => {
    clearCsrfToken()
    setUser(null)
    setAuthState('unauthenticated')
    setAuthError(message)
  }, [])
  const handleUnauthorized = useCallback(() => expireSession(), [expireSession])

  useEffect(() => {
    let cancelled = false
    void apiFetch<AuthResponse>('/api/auth/me').then(data => {
      if (cancelled) return
      const token = authToken(data)
      if (token) setCsrfToken(token)
      setUser(data.user)
      setAuthState('authenticated')
    }).catch(caught => {
      if (cancelled) return
      if (isUnauthorized(caught)) expireSession('请登录后继续。')
      else { setAuthError(errorMessage(caught, '无法连接认证服务。')); setAuthState('unauthenticated') }
    })
    return () => { cancelled = true }
  }, [expireSession])

  const login = (username: string, password: string) => {
    setAuthBusy(true); setAuthError(''); clearCsrfToken()
    void apiFetch<AuthResponse>('/api/auth/login', jsonBody({ username, password })).then(data => {
      const token = authToken(data)
      if (token) setCsrfToken(token)
      setUser(data.user)
      setAuthState('authenticated')
    }).catch(caught => setAuthError(errorMessage(caught, '登录失败'))).finally(() => setAuthBusy(false))
  }

  const changePassword = async (currentPassword: string, newPassword: string) => {
    try {
      const data = await apiFetch<AuthResponse>('/api/auth/change-password', jsonBody({ currentPassword, newPassword }))
      const token = authToken(data)
      if (token) setCsrfToken(token)
    } catch (caught) {
      if (isUnauthorized(caught)) expireSession()
      throw caught
    }
  }

  const logout = async () => {
    try { await apiFetch('/api/auth/logout', { method: 'POST' }) }
    finally {
      clearCsrfToken(); setUser(null); setAuthState('unauthenticated'); setAuthError('')
    }
  }

  if (authState === 'loading') return <main className="auth-page"><div className="auth-loading">正在验证登录状态…</div></main>
  if (!user) return <LoginScreen busy={authBusy} error={authError} onSubmit={login} />
  return <AuthenticatedShell user={user} onUnauthorized={handleUnauthorized} onChangePassword={changePassword} onLogout={logout} />
}
