import { describe, it, expect } from "vitest";
import { LocalRing } from "../src/local-ring.js";

describe("LocalRing", () => {
  it("assigns monotonically increasing local_seq starting at 1", () => {
    const ring = new LocalRing<string>(10);
    expect(ring.push("a").localSeq).toBe(1);
    expect(ring.push("b").localSeq).toBe(2);
    expect(ring.push("c").localSeq).toBe(3);
  });

  it("peekFrom returns entries above ackedSeq, capped at n", () => {
    const ring = new LocalRing<string>(10);
    ring.push("a"); ring.push("b"); ring.push("c");
    const batch = ring.peekFrom(0, 2);
    expect(batch.map(e => e.payload)).toEqual(["a", "b"]);
    expect(ring.peekFrom(1, 10).map(e => e.payload)).toEqual(["b", "c"]);
  });

  it("evictThrough removes entries with localSeq <= seq", () => {
    const ring = new LocalRing<string>(10);
    ring.push("a"); ring.push("b"); ring.push("c");
    ring.evictThrough(2);
    expect(ring.size).toBe(1);
    expect(ring.peekFrom(0, 10).map(e => e.payload)).toEqual(["c"]);
  });

  it("evicts oldest entries when capacity exceeded", () => {
    const ring = new LocalRing<string>(2);
    ring.push("a"); ring.push("b"); ring.push("c");
    expect(ring.size).toBe(2);
    expect(ring.peekFrom(0, 10).map(e => e.payload)).toEqual(["b", "c"]);
  });

  it("continues to allocate seq even after capacity overflow", () => {
    const ring = new LocalRing<string>(2);
    ring.push("a"); ring.push("b"); ring.push("c");
    expect(ring.push("d").localSeq).toBe(4);
  });
});
