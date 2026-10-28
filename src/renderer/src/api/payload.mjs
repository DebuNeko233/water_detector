/**
 * Safely cross Electron's structured-clone IPC boundary.
 *
 * Vue 3 wraps UI configuration objects in Proxy, which Electron cannot clone.
 * The Python API accepts JSON only, so use its wire format as the
 * normalization boundary. This removes reactive proxies recursively.
 */
export function serializeBody(body) {
  if (body === undefined) return { json: undefined, plain: undefined }
  const json = JSON.stringify(body)
  return { json, plain: json === undefined ? undefined : JSON.parse(json) }
}
