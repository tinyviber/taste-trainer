export type Exercise = {
  id: string
  title: string
  status: string
  score?: number | null
  weakest?: string
}

export type Video = { name: string; inbox: boolean }

export type Job = { status: string; detail?: string; started?: number }

export type ExerciseDocuments = {
  prompt: string
  submission: string
  review: string
  micro_revision: string
  micro_feedback: string
  revision: string
}

export type TrainerState = {
  latest_exercise: Exercise | null
  exercises: Exercise[]
  videos: Video[]
  jobs: Record<string, Job>
  submission_template: string
  micro_focus: { dim?: string; original?: string; gap?: string } | null
  documents: ExerciseDocuments
  a2h_url: string
}

export type Principle = {
  id: string
  title: string
  detail: string
  evidence?: unknown
  source_video?: string
  status?: string
}

export type PrinciplesState = {
  pending: Principle[]
  accepted: Principle[]
  active?: Principle[]
  inactive?: Principle[]
  ledger?: Principle[]
}

export type ModelInfo = { id: string; owned_by?: string }

export type Provider = {
  id: string
  name: string
  baseUrl: string
  hasApiKey: boolean
  models: ModelInfo[]
  selectedModelIds: string[]
  lastDiscoveryAt?: string
}

export type ProviderInput = {
  id?: string
  name: string
  baseUrl: string
  apiKey?: string
  models?: ModelInfo[]
  selectedModelIds?: string[]
}

export type UserSettings = {
  default_provider_id: string
  default_model_id: string
}

export type AuthUser = {
  id?: string
  username: string
}

export type AuthResponse = {
  user: AuthUser
  csrfToken?: string
  csrf_token?: string
}
