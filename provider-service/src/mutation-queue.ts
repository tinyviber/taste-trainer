export function createMutationQueue() {
  const queues = new Map<string, Promise<void>>()

  return function queueMutation<T>(userId: string, mutation: () => Promise<T>): Promise<T> {
    // Queue the complete read -> modify -> write transaction.  Queuing only
    // saveProviders() would let two requests calculate from the same stale
    // snapshot and lose one of the changes.
    const current = queues.get(userId) ?? Promise.resolve()
    const next = current.catch(() => undefined).then(() => mutation())
    const marker = next.then(() => undefined, () => undefined)
    queues.set(userId, marker)
    void marker.then(() => {
      if (queues.get(userId) === marker) queues.delete(userId)
    })
    return next
  }
}
