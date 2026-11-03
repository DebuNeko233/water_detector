/**
 * Validate all renderer-provided request targets before concatenating the local
 * FastAPI origin. Renderer can only reach /api/*; arbitrary URLs, encoded paths,
 * fragments, and query injection are rejected.
 *
 * Only the video state polling endpoint currently needs a query string.
 */
const API_PATH = /^\/api\/[a-zA-Z0-9/_-]+$/
const STATE_QUERY = /^after_revision=(?:-1|0|[1-9][0-9]{0,9})$/

export function validateApiPath(apiPath, method) {
  if (typeof apiPath !== 'string' || apiPath.length > 256 || apiPath.includes('..')) {
    throw new Error('Invalid API path')
  }
  const queryStart = apiPath.indexOf('?')
  const pathname = queryStart < 0 ? apiPath : apiPath.slice(0, queryStart)
  if (!API_PATH.test(pathname)) throw new Error('Invalid API path')
  if (queryStart >= 0) {
    const query = apiPath.slice(queryStart + 1)
    if (method !== 'GET' || pathname !== '/api/playback/state' || !STATE_QUERY.test(query)) {
      throw new Error('Invalid API path')
    }
  }
  return apiPath
}
