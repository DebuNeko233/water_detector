import assert from 'node:assert/strict'
import test from 'node:test'
import { validateApiPath } from '../../src/main/api-path.mjs'

test('video polling revision query is accepted by Electron IPC', () => {
  for (const rev of ['-1', '0', '1', '42', '1234567890']) {
    const url = '/api/playback/state?after_revision=' + rev
    assert.equal(validateApiPath(url, 'GET'), url)
  }
  assert.equal(validateApiPath('/api/playback/start', 'POST'), '/api/playback/start')
  assert.equal(validateApiPath('/api/config/active', 'GET'), '/api/config/active')
})

test('IPC cannot request arbitrary URLs or smuggle unexpected query parameters', () => {
  const bad = [
    'http://evil.example/api/playback/state',
    '//evil.example/api/health',
    '/api/../internal/shutdown',
    '/api/%2e%2e/internal/shutdown',
    '/api/playback/state#fragment',
    '/api/playback/state?after_revision=1&url=http://evil.example',
    '/api/playback/state?after_revision=1%26url%3Devil',
    '/api/playback/state?other=1',
    '/api/playback/state?after_revision=1?x=2',
    '/api/playback/state?after_revision=-2',
    '/api/playback/state?after_revision=1.5',
    '/api/playback/state?after_revision=+1',
    '/api/playback/state?after_revision=01',
    '/api/config/active?after_revision=1',
    '/internal/shutdown',
    '/api/playback/state?after_revision=' + '9'.repeat(50),
  ]
  for (const url of bad) {
    assert.throws(() => validateApiPath(url, 'GET'), /Invalid API path/, url)
  }
  assert.throws(
    () => validateApiPath('/api/playback/state?after_revision=9', 'POST'),
    /Invalid API path/,
  )
})
