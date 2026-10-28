/** The request body is JSON-serializable data (not a Vue Proxy). */
export declare function serializeBody(body: unknown): { json: string | undefined; plain: unknown }
