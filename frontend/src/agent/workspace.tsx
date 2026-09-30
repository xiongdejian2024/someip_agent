import { createContext, useContext, useEffect } from 'react'
import type { AgentWorkspaceContext } from '../types'

interface WorkspaceActions {
  setScope: (scope: AgentWorkspaceContext) => void
  openAgent: (prompt?: string) => void
}

export const AgentWorkspace = createContext<WorkspaceActions>({
  setScope: () => undefined,
  openAgent: () => undefined,
})

/** 对象选择改变才同步引用；实时收帧不会重复触发上下文更新。 */
export function useAgentScope(scope: AgentWorkspaceContext) {
  const { setScope, openAgent } = useContext(AgentWorkspace)
  const serialized = JSON.stringify(scope)
  useEffect(() => { setScope(JSON.parse(serialized) as AgentWorkspaceContext) }, [serialized, setScope])
  return openAgent
}

export function protocolId(value: string | undefined): number | undefined {
  if (!value || !/^(?:0x[\da-f]+|\d+)$/i.test(value)) return undefined
  const id = Number(value)
  return Number.isInteger(id) && id >= 0 && id <= 65535 ? id : undefined
}
