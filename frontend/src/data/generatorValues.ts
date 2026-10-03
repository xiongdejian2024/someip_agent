import { parseJson } from '../api/json'
import type { SimulationStartRequest } from '../types'

export type GeneratorNumber = number | bigint
export type GeneratorDataType = SimulationStartRequest['generator']['data_type']

export function isGeneratorNumber(value: unknown): value is GeneratorNumber {
  return typeof value === 'bigint' || (typeof value === 'number' && Number.isFinite(value)
    && (!Number.isInteger(value) || Number.isSafeInteger(value)))
}

export function validateGeneratorValue(value: unknown, dataType: GeneratorDataType): GeneratorNumber {
  if (!isGeneratorNumber(value)) throw new Error('数值必须有限；大整数须使用精确十进制输入')
  if (dataType.includes('int') || dataType === 'boolean') {
    if (typeof value === 'number' && !Number.isInteger(value)) throw new Error(dataType + ' 需要整数值')
    const exact = BigInt(value)
    const unsigned = dataType.startsWith('uint') || dataType === 'boolean'
    const bits = dataType === 'boolean' ? 1 : Number(dataType.replace(/\D/g, ''))
    const minimum = unsigned ? 0n : -(1n << BigInt(bits - 1))
    const maximum = (1n << BigInt(unsigned ? bits : bits - 1)) - 1n
    if (exact < minimum || exact > maximum) throw new Error(dataType + ' 数值超出类型范围')
  } else if (typeof value === 'bigint') {
    throw new Error('浮点激励不接受超出安全整数范围的整数；请明确提供可表示的浮点值')
  } else if (dataType === 'float32' && !Number.isFinite(Math.fround(value))) {
    throw new Error('float32 数值溢出')
  }
  return value
}

export function parseGeneratorValue(text: string, dataType: GeneratorDataType): GeneratorNumber {
  if (!text.trim()) throw new Error('数值不能为空')
  return validateGeneratorValue(parseJson(text.trim()), dataType)
}

export function generatorMidpoint(minimum: GeneratorNumber, maximum: GeneratorNumber, dataType: GeneratorDataType): GeneratorNumber {
  if (dataType === 'boolean') return minimum
  if (dataType.includes('int') && [minimum, maximum].every(value => typeof value === 'bigint' || Number.isSafeInteger(value))) {
    // 与 Math.round 一致：负数半整数朝正无穷取整，且不经过浮点。
    const sum = BigInt(minimum) + BigInt(maximum)
    const midpoint = sum / 2n + (sum > 0n && sum % 2n !== 0n ? 1n : 0n)
    return midpoint >= BigInt(Number.MIN_SAFE_INTEGER) && midpoint <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(midpoint) : midpoint
  }
  const midpoint = Number(minimum) / 2 + Number(maximum) / 2
  return dataType.includes('int') ? Math.round(midpoint) : midpoint
}

export function generatorSliderSafe(minimum: GeneratorNumber, maximum: GeneratorNumber, value: GeneratorNumber): boolean {
  return [minimum, maximum, value].every(item => typeof item === 'number' && isGeneratorNumber(item))
}

export function validateGeneratorRange(dataType: GeneratorDataType, kind: string, minimum: GeneratorNumber, maximum: GeneratorNumber, initial: GeneratorNumber): void {
  for (const value of [minimum, maximum, initial]) validateGeneratorValue(value, dataType)
  if (minimum > maximum || initial < minimum || initial > maximum) throw new Error('激励范围或初始值无效')
  if ((kind === 'sine' || kind === 'ramp') && dataType.includes('int')
    && !generatorSliderSafe(minimum, maximum, initial)) {
    throw new Error('整数正弦/斜坡须在安全整数范围内；精确大整数请使用常量或服务模型的序列/随机源')
  }
}

export function validateGeneratorStep(dataType: GeneratorDataType, at: unknown, after: unknown, minimum: GeneratorNumber, maximum: GeneratorNumber): void {
  validateGeneratorValue(at, 'uint64')
  const value = validateGeneratorValue(after, dataType)
  if (value < minimum || value > maximum) throw new Error('阶跃后值超出激励范围')
}

export function validateGeneratorSequence(value: unknown, dataType: GeneratorDataType, minimum: GeneratorNumber, maximum: GeneratorNumber): GeneratorNumber[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > 8192) throw new Error('序列须为包含 1–8192 项的 JSON 数组')
  return value.map((item, index) => {
    const exact = validateGeneratorValue(item, dataType)
    if (exact < minimum || exact > maximum) throw new Error('序列第 ' + (index + 1) + ' 项超出激励范围')
    return exact
  })
}

export function parseGeneratorSequence(text: string, dataType: GeneratorDataType, minimum: GeneratorNumber, maximum: GeneratorNumber): GeneratorNumber[] {
  if (text.length > 262144) throw new Error('序列文本不能超过 256 KiB 字符')
  return validateGeneratorSequence(parseJson(text), dataType, minimum, maximum)
}
