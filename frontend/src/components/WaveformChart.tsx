import { LineChart } from 'echarts/charts'
import { DataZoomComponent, GridComponent, LegendComponent, TitleComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { useEffect, useRef } from 'react'
import type { WaveSample } from '../types'

use([LineChart, DataZoomComponent, GridComponent, LegendComponent, TitleComponent, TooltipComponent, CanvasRenderer])

export const SIGNAL_COLORS = ['#57f0c9', '#78c4ff', '#ffd07a', '#c7adff', '#ff91ac', '#8fe0ed']

interface WaveformChartProps {
  samples: WaveSample[]
  compact?: boolean
  signalKeys?: string[]
  layout?: 'lanes' | 'overlay'
  windowSeconds?: number
  height?: number
  showZoom?: boolean
  resetZoom?: number
}

export function signalLabel(key: string): string {
  const parts = key.split('/')
  return parts.length >= 3 ? parts.slice(2).join('/') : key
}

export function formatSignalValue(value: number | undefined): string {
  if (value === undefined || !Number.isFinite(value)) return '—'
  const absolute = Math.abs(value)
  if (absolute >= 100_000 || (absolute > 0 && absolute < 0.001)) return value.toExponential(2)
  return Number(value.toPrecision(6)).toLocaleString('en-US', { maximumFractionDigits: 5 })
}

export function WaveformChart({ samples, compact = false, signalKeys, layout = 'overlay', windowSeconds = 0, height, showZoom = false, resetZoom = 0 }: WaveformChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ECharts | null>(null)
  const zoomRef = useRef({ start: 0, end: 100 })
  const configurationRef = useRef('')
  const chartHeight = height ?? (compact ? 185 : 300)

  useEffect(() => {
    if (!containerRef.current) return
    const chart = init(containerRef.current, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    chart.on('datazoom', () => {
      const option = chart.getOption() as { dataZoom?: { start?: number; end?: number }[] }
      const zoom = option.dataZoom?.[0]
      if (zoom?.start !== undefined && zoom.end !== undefined) zoomRef.current = { start: zoom.start, end: zoom.end }
    })
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(containerRef.current)
    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const lastTime = samples.at(-1)?.time ?? Date.now()
    const visible = windowSeconds > 0 ? samples.filter((sample) => sample.time >= lastTime - windowSeconds * 1000) : samples
    const keys = (signalKeys ?? Array.from(new Set(visible.flatMap((sample) => Object.keys(sample.values))))).slice(0, compact && !signalKeys ? 3 : 6)
    const configuration = JSON.stringify([keys, layout, windowSeconds, resetZoom])
    if (configurationRef.current !== configuration) {
      configurationRef.current = configuration
      zoomRef.current = { start: 0, end: 100 }
    }
    const lanes = layout === 'lanes' && keys.length > 0
    const count = lanes ? keys.length : 1
    const top = lanes ? 24 : compact ? 14 : 34
    const bottom = showZoom ? 62 : 30
    const trackHeight = (chartHeight - top - bottom) / count
    const indices = Array.from({ length: count }, (_, index) => index)
    chart.setOption({
      animation: false,
      backgroundColor: 'transparent',
      color: SIGNAL_COLORS,
      title: !keys.length ? [{ text: '选择信号以查看波形', left: 'center', top: 'middle', textStyle: { color: '#c5d4d8', fontSize: 12, fontWeight: 400 } }]
        : lanes ? keys.map((key, index) => ({ text: `${index + 1}  ${signalLabel(key).length > 28 ? `${signalLabel(key).slice(0, 27)}…` : signalLabel(key)}`, left: 70, top: top + index * trackHeight - 20, textStyle: { color: SIGNAL_COLORS[index], fontSize: 10, fontWeight: 500 } })) : [],
      tooltip: {
        trigger: 'axis', confine: true, renderMode: 'richText',
        backgroundColor: 'rgba(38, 62, 72, .98)', borderColor: '#7e99a3',
        textStyle: { color: '#eef6f8', fontSize: 11 },
        axisPointer: { type: 'line', lineStyle: { color: '#c5d4d8', type: 'dashed' } },
        valueFormatter: (value: number) => formatSignalValue(value),
      },
      legend: {
        show: !lanes && !compact && signalKeys === undefined,
        type: 'scroll', top: 3, left: 60, right: 16,
        itemWidth: 13, itemHeight: 3,
        formatter: (name: string) => name.length > 22 ? `${name.slice(0, 21)}…` : name,
        textStyle: { color: '#d8e4e6', fontSize: 10 },
        pageTextStyle: { color: '#d8e4e6' },
      },
      grid: indices.map((index) => ({ left: 68, right: 22, top: top + index * trackHeight, height: trackHeight - (lanes ? 26 : 0) })),
      xAxis: indices.map((index) => ({
        gridIndex: index, type: 'time', boundaryGap: false,
        min: visible.length > 1 ? visible[0].time : undefined,
        max: visible.length > 1 ? lastTime : undefined,
        axisLabel: { show: index === count - 1, color: '#d1dfe2', fontSize: 10, hideOverlap: true },
        axisLine: { lineStyle: { color: '#7f98a1' } },
        axisTick: { show: false }, splitLine: { show: false },
      })),
      yAxis: indices.map((index) => ({
        gridIndex: index, type: 'value', scale: true, splitNumber: lanes ? 2 : 4,
        axisLabel: { color: '#d1dfe2', fontSize: 10, formatter: (value: number) => formatSignalValue(value), width: 59, overflow: 'truncate' },
        axisLine: { show: false },
        splitLine: { lineStyle: { color: 'rgba(197, 212, 216, .20)' } },
      })),
      dataZoom: showZoom ? [
        { type: 'inside', xAxisIndex: indices, filterMode: 'none', ...zoomRef.current, zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: false },
        { type: 'slider', xAxisIndex: indices, filterMode: 'none', ...zoomRef.current, left: 68, right: 22, bottom: 8, height: 18, showDetail: false, borderColor: '#6f8992', fillerColor: 'rgba(87, 240, 201, .15)', textStyle: { color: '#d1dfe2' } },
      ] : [],
      series: keys.map((key, index) => ({
        id: key, name: signalLabel(key), type: 'line',
        xAxisIndex: lanes ? index : 0, yAxisIndex: lanes ? index : 0,
        showSymbol: false, connectNulls: false, smooth: false, sampling: 'lttb',
        lineStyle: { width: 1.7 },
        data: visible.filter((sample) => Number.isFinite(sample.values[key])).map((sample) => [sample.time, sample.values[key]]),
      })),
    }, true)
  }, [chartHeight, compact, layout, resetZoom, samples, showZoom, signalKeys, windowSeconds])

  return <div ref={containerRef} className={`wave-chart${compact ? ' compact' : ''}`} style={{ height: chartHeight }} role="img" aria-label={layout === 'lanes' ? '分轨信号波形图，各信号独立纵轴' : '叠加信号波形图，共用纵轴'} />
}
