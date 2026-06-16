import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { CodieEmitter } from "../src/emitter.js";
import type { HostSession } from "../src/host-session.js";

const mkLogger = () => ({
  debug: vi.fn(),
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
});

const mkSession = (callToolImpl: HostSession["callTool"]): HostSession => ({
  callTool: callToolImpl,
  invalidate: vi.fn(async () => {}),
} as unknown as HostSession);

describe("CodieEmitter", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("flushes pending envelopes to host_agent_event_report", async () => {
    const callTool = vi.fn(async () => ({ last_local_seq: 1, accepted: 1 }));
    const session = mkSession(callTool);
    const emitter = new CodieEmitter("inst-1", session, mkLogger());

    await emitter.start();
    emitter.emit({ type: "chat_start", run_id: "r1", ts_ms: 100, data: {} });

    // wait one flush tick
    await vi.runOnlyPendingTimersAsync();
    await Promise.resolve();
    await Promise.resolve();

    expect(callTool).toHaveBeenCalledWith("host_agent_event_report", {
      category: "activity",
      instance_id: "inst-1",
      events: [
        { type: "chat_start", run_id: "r1", ts_ms: 100, data: {}, local_seq: 1 },
      ],
    });
    await emitter.stop();
  });

  it("evicts acked entries after successful flush (ack under structuredContent)", async () => {
    // Regression for the event-flood bug: the real MCP SDK wraps the sidecar's
    // {last_local_seq,accepted} ack under `.structuredContent`. The emitter must
    // read it there — reading the top level left last_local_seq undefined, the
    // ring never evicted, and every flush re-sent the backlog from seq 0.
    // Mock returns ack reflecting the highest local_seq so the loop converges.
    const callTool = vi.fn(async (_tool: string, params: Record<string, unknown>) => {
      const events = (params as { events: Array<{ local_seq: number }> }).events;
      const last = events[events.length - 1].local_seq;
      return { structuredContent: { last_local_seq: last, accepted: events.length } };
    });
    const emitter = new CodieEmitter("inst-1", mkSession(callTool), mkLogger());
    await emitter.start();
    emitter.emit({ type: "chat_start", run_id: "r1", ts_ms: 100, data: {} });
    emitter.emit({ type: "chat_end", run_id: "r1", ts_ms: 200, data: {} });
    await vi.advanceTimersByTimeAsync(0);
    await Promise.resolve();
    await Promise.resolve();
    // second call would only see events with local_seq > 2 (none)
    callTool.mockClear();
    emitter.emit({ type: "tool_start", run_id: "r1", ts_ms: 300, data: {} });
    await vi.advanceTimersByTimeAsync(0);
    await Promise.resolve();
    await Promise.resolve();
    expect(callTool).toHaveBeenCalledWith(
      "host_agent_event_report",
      expect.objectContaining({
        events: [expect.objectContaining({ local_seq: 3 })],
      }),
    );
    await emitter.stop();
  });

  it("evicts the sent batch when the ack omits last_local_seq (no re-send flood)", async () => {
    // The live flood (openclaw): the sidecar's {accepted,last_local_seq} dict is
    // returned by a bare `@mcp.tool() -> dict` with no output schema, so the MCP
    // SDK delivers it as *text content* — NOT under .structuredContent and NOT as
    // a flat field. With last_local_seq unreadable the emitter left last=0, never
    // evicted, and re-sent the same backlog every backoff tick forever (a finished
    // run's chat_start kept reviving the 2s "thinking" bubble). Mirror the hermes
    // emitter: on a successful (non-error) call, fall back to evicting through the
    // max local_seq just sent so the ring always makes forward progress.
    const callTool = vi.fn(async () => ({
      content: [{ type: "text", text: '{"accepted":2,"last_local_seq":2}' }],
    }));
    const emitter = new CodieEmitter("inst-1", mkSession(callTool), mkLogger());
    await emitter.start();
    emitter.emit({ type: "chat_start", run_id: "r1", ts_ms: 100, data: {} });
    emitter.emit({ type: "chat_end", run_id: "r1", ts_ms: 200, data: {} });
    await vi.advanceTimersByTimeAsync(0);
    await Promise.resolve();
    await Promise.resolve();
    callTool.mockClear();
    emitter.emit({ type: "tool_start", run_id: "r1", ts_ms: 300, data: {} });
    await vi.advanceTimersByTimeAsync(0);
    await Promise.resolve();
    await Promise.resolve();
    // The next flush must carry ONLY the new event (seq 3); seqs 1-2 evicted.
    expect(callTool).toHaveBeenCalledWith(
      "host_agent_event_report",
      expect.objectContaining({
        events: [expect.objectContaining({ local_seq: 3 })],
      }),
    );
    await emitter.stop();
  });

  it("retains the batch when the sidecar reports a tool error (isError)", async () => {
    // A tool-level error (isError:true, not a transport throw) means the events
    // were NOT accepted — keep them and retry; do NOT let the max-seq fallback
    // evict undelivered events.
    const callTool = vi.fn(async () => ({
      isError: true,
      content: [{ type: "text", text: "boom" }],
    }));
    const emitter = new CodieEmitter("inst-1", mkSession(callTool), mkLogger());
    await emitter.start();
    emitter.emit({ type: "chat_start", run_id: "r1", ts_ms: 100, data: {} });
    await vi.advanceTimersByTimeAsync(0);
    await Promise.resolve();
    await Promise.resolve();
    callTool.mockClear();
    // Wake the loop again; the same event must be retried (still seq 1).
    emitter.emit({ type: "chat_end", run_id: "r1", ts_ms: 200, data: {} });
    await vi.advanceTimersByTimeAsync(0);
    await Promise.resolve();
    await Promise.resolve();
    expect(callTool).toHaveBeenCalledWith(
      "host_agent_event_report",
      expect.objectContaining({
        events: [
          expect.objectContaining({ local_seq: 1 }),
          expect.objectContaining({ local_seq: 2 }),
        ],
      }),
    );
    await emitter.stop();
  });

  it("invalidates session and retains batch on flush failure", async () => {
    const callTool = vi.fn(async () => { throw new Error("transport boom"); });
    const session = mkSession(callTool);
    const logger = mkLogger();
    const emitter = new CodieEmitter("inst-1", session, logger);
    await emitter.start();
    emitter.emit({ type: "chat_start", run_id: "r1", ts_ms: 100, data: {} });
    // Pump microtasks without advancing the backoff timer — otherwise the
    // loop fires another flush attempt and invalidate is called twice.
    await vi.advanceTimersByTimeAsync(0);
    await Promise.resolve();
    await Promise.resolve();
    expect(session.invalidate).toHaveBeenCalledTimes(1);
    expect(logger.warn).toHaveBeenCalled();
    await emitter.stop();
  });

  it("times out callTool after 5s and invalidates", async () => {
    const callTool = vi.fn(() => new Promise(() => { /* never resolves */ }));
    const session = mkSession(callTool);
    const emitter = new CodieEmitter("inst-1", session, mkLogger());
    await emitter.start();
    emitter.emit({ type: "chat_start", run_id: "r1", ts_ms: 100, data: {} });
    // advance past the 5s timeout
    await vi.advanceTimersByTimeAsync(5500);
    await Promise.resolve();
    expect(session.invalidate).toHaveBeenCalledTimes(1);
    await emitter.stop();
  });
});
