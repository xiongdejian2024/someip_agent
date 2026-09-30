type LogContext = Record<string, unknown>

export function logInfo(event: string, context: LogContext = {}) {
  console.info(`[SOME/IP Console] ${event}`, context)
}
export function logError(event: string, error: unknown, context: LogContext = {}) {
  const normalized = error instanceof Error
    ? { name: error.name, message: error.message, stack: error.stack }
    : { name: 'UnknownError', message: String(error), stack: undefined }
  console.error(`[SOME/IP Console] ${event}`, { ...context, exception: normalized })
}
