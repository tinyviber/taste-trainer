import assert from 'node:assert/strict'
import test from 'node:test'
import { createMutationQueue } from '../src/mutation-queue.js'

test('same-user read-modify-write mutations preserve concurrent updates', async () => {
  const queueMutation = createMutationQueue()
  const values: string[] = []
  let markSnapshotTaken!: () => void
  const snapshotTaken = new Promise<void>(resolve => { markSnapshotTaken = resolve })
  let releaseFirst!: () => void
  const firstGate = new Promise<void>(resolve => { releaseFirst = resolve })

  const first = queueMutation('user-a', async () => {
    const snapshot = [...values]
    markSnapshotTaken()
    await firstGate
    values.splice(0, values.length, ...snapshot, 'x')
  })
  await snapshotTaken

  let secondFinished = false
  const second = queueMutation('user-a', async () => {
    const snapshot = [...values]
    values.splice(0, values.length, ...snapshot, 'y')
    secondFinished = true
  })
  await new Promise<void>(resolve => setImmediate(resolve))
  assert.equal(secondFinished, false)

  releaseFirst()
  await Promise.all([first, second])
  assert.deepEqual(values, ['x', 'y'])
})

test('different users do not block each other', async () => {
  const queueMutation = createMutationQueue()
  let releaseFirst!: () => void
  const firstGate = new Promise<void>(resolve => { releaseFirst = resolve })
  let secondStarted = false

  const first = queueMutation('user-a', async () => {
    await firstGate
  })
  const second = queueMutation('user-b', async () => {
    secondStarted = true
  })

  await second
  assert.equal(secondStarted, true)
  releaseFirst()
  await first
})
