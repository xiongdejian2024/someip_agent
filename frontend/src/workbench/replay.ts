import type { RawMonitorMessage } from '../api/adapters'

export interface ReplayFrame { index: number; offset_ms: number; message: RawMonitorMessage }
export interface ReplayPage { frames: ReplayFrame[]; next_offset: number; complete: boolean; wire_replay: false }

/** 仅调度离线帧；每页最多 1000 帧，每次最多释放 256 帧，没有网络发送依赖。 */
export class ReplayTimeline {
  private frames: ReplayFrame[] = []
  private cursor = 0
  private lastWall = 0
  private lastIndex = -1
  private lastOffset = -1
  position = 0
  rate = 1
  playing = false
  complete = false
  nextOffset = 0
  get buffered() { return this.frames.length - this.cursor }
  get ended() { return this.complete && this.buffered === 0 }
  reset(position = 0) {
    if (!Number.isFinite(position) || position < 0) throw new Error('回放位置无效')
    this.frames = []; this.cursor = 0; this.lastIndex = -1; this.lastOffset = -1
    this.position = position; this.complete = false; this.nextOffset = 0; this.playing = false
  }
  load(page: ReplayPage) {
    if (page.wire_replay !== false || this.buffered || page.frames.length > 1000) throw new Error('回放页状态或容量无效')
    for (const frame of page.frames) {
      if (!Number.isSafeInteger(frame.index) || frame.index <= this.lastIndex || !Number.isFinite(frame.offset_ms) || frame.offset_ms < this.lastOffset) throw new Error('回放帧顺序或时间轴非法')
      this.lastIndex = frame.index; this.lastOffset = frame.offset_ms
    }
    if (!Number.isSafeInteger(page.next_offset) || page.next_offset < this.nextOffset || page.next_offset < this.lastIndex + 1) throw new Error('回放游标不能倒退')
    this.frames = page.frames; this.cursor = 0; this.complete = page.complete; this.nextOffset = page.next_offset
  }
  play(now: number) { this.lastWall = now; this.playing = !this.ended }
  pause() { this.playing = false }
  setRate(rate: number, now: number) {
    if (!Number.isFinite(rate) || rate < 0.1 || rate > 10) throw new Error('回放倍率必须在 0.1–10 之间')
    const released = this.advance(now); this.rate = rate; return released
  }
  step(): ReplayFrame[] {
    this.pause(); const frame = this.frames[this.cursor]
    if (!frame) return []
    this.cursor += 1; this.position = frame.offset_ms; return [frame]
  }
  advance(now: number): ReplayFrame[] {
    if (!Number.isFinite(now) || now < this.lastWall) throw new Error('回放时钟不能倒退')
    if (this.playing && this.buffered) this.position += (now - this.lastWall) * this.rate
    this.lastWall = now
    const released: ReplayFrame[] = []
    if (!this.playing) return released
    while (this.buffered && released.length < 256 && this.frames[this.cursor].offset_ms <= this.position) released.push(this.frames[this.cursor++])
    if (this.ended) this.pause()
    return released
  }
}
