import { isInteger, parse, parseLosslessNumber, stringify } from 'lossless-json'

/** 小整数仍用 number；大整数字面量用 bigint，保持协议字段和业务值的语义。 */
export function parseJson(text: string): unknown {
  return parse(text, undefined, {
    parseNumber: (literal: string) => {
      const numeric = Number(literal)
      if (isInteger(literal)) return Number.isSafeInteger(numeric) ? numeric : BigInt(literal)
      if (!Number.isFinite(numeric) || (Number.isInteger(numeric) && !Number.isSafeInteger(numeric))) {
        throw new Error('数值超出安全范围；大整数请使用完整十进制整数字面量，不使用指数或小数格式')
      }
      return parseLosslessNumber(literal).valueOf()
    },
  })
}

/** bigint 写为精确 JSON 数字，不写成字符串，不使用全局原型补丁。 */
export function stringifyJson(value: unknown, space?: number): string {
  return stringify(value, undefined, space) ?? ''
}
