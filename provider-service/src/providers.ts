import { createOpenAICompatible } from '@ai-sdk/openai-compatible'
import { createProviderRegistry, customProvider } from 'ai'

export type ProviderConfig = {
  id: string
  name: string
  baseUrl: string
  apiKey: string
  selectedModelIds: string[]
}

/**
 * Build one AI SDK provider from a user-configured OpenAI-compatible endpoint.
 * The API key stays in this Node process; the browser receives only model ids.
 */
export function createConfiguredProvider(config: ProviderConfig, fetchImpl: typeof fetch = fetch) {
  const compatible = createOpenAICompatible({
    name: `taste-${config.id}`,
    apiKey: config.apiKey,
    baseURL: config.baseUrl,
    includeUsage: true,
    fetch: fetchImpl,
  })
  return customProvider({
    languageModels: Object.fromEntries(
      config.selectedModelIds.map(modelId => [modelId, compatible(modelId)]),
    ),
  })
}

export function createConfiguredRegistry(configs: ProviderConfig[], fetchImpl: typeof fetch = fetch) {
  return createProviderRegistry(
    Object.fromEntries(configs.map(config => [config.id, createConfiguredProvider(config, fetchImpl)])),
  )
}
