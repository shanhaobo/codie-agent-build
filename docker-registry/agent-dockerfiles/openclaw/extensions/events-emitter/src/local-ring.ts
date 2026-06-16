export interface RingEntry<T> {
  localSeq: number;
  payload: T;
}

export class LocalRing<T> {
  private buf: RingEntry<T>[] = [];
  private nextSeq = 1;

  constructor(private readonly capacity: number) {
    if (capacity < 1) throw new Error("LocalRing capacity must be >= 1");
  }

  push(payload: T): RingEntry<T> {
    const entry = { localSeq: this.nextSeq++, payload };
    this.buf.push(entry);
    if (this.buf.length > this.capacity) this.buf.shift();
    return entry;
  }

  peekFrom(ackedSeq: number, n: number): RingEntry<T>[] {
    const out: RingEntry<T>[] = [];
    for (const e of this.buf) {
      if (e.localSeq > ackedSeq) {
        out.push(e);
        if (out.length >= n) break;
      }
    }
    return out;
  }

  evictThrough(seq: number): void {
    this.buf = this.buf.filter(e => e.localSeq > seq);
  }

  get size(): number {
    return this.buf.length;
  }
}
