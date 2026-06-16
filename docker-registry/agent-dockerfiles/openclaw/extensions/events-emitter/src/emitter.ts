import { EventEmitter } from "node:events";
import { LocalRing } from "./local-ring.js";
import type { HostSession } from "./host-session.js";
import type { Envelope, ReportPayload, ReportResponse } from "./envelope.js";

const BATCH_SIZE = 50;
const BACKOFF_INITIAL_MS = 1000;
const BACKOFF_MAX_MS = 30000;
const CALL_SIDECAR_TIMEOUT_MS = 5000;
const RING_CAPACITY = 200;

// Subset of api.logger we use; declared inline to avoid leaking SDK types here.
export interface EmitterLogger {
  warn(msg: string, extra?: Record<string, unknown>): void;
}

export class CodieEmitter {
  private readonly ring = new LocalRing<Envelope>(RING_CAPACITY);
  private ackedSeq = 0;
  private readonly wake = new EventEmitter();
  private stopping = false;
  private task: Promise<void> | null = null;
  private backoffMs = BACKOFF_INITIAL_MS;

  constructor(
    private readonly instanceId: string,
    private readonly session: HostSession,
    private readonly logger: EmitterLogger,
  ) {
    this.wake.setMaxListeners(0);
  }

  emit(envelope: Envelope): void {
    this.ring.push(envelope);
    this.wake.emit("wake");
  }

  async start(): Promise<void> {
    if (this.task) return;
    this.stopping = false;
    this.task = this.runLoop();
  }

  async stop(): Promise<void> {
    this.stopping = true;
    this.wake.emit("wake");
    if (this.task) await this.task;
    this.task = null;
    await this.session.invalidate();
  }

  private async runLoop(): Promise<void> {
    while (!this.stopping) {
      try {
        const sent = await this.flushOnce();
        if (sent > 0) {
          this.backoffMs = BACKOFF_INITIAL_MS;
          continue;
        }
        await this.waitWakeOrTimeout(this.backoffMs);
        this.backoffMs = Math.min(this.backoffMs * 2, BACKOFF_MAX_MS);
      } catch (err) {
        // Top-level catch-all — spec §2.5 守护语义: never propagate
        this.logger.warn(`emitter loop iteration failed: ${(err as Error).message}`);
        await this.waitWakeOrTimeout(this.backoffMs);
      }
    }
  }

  private async flushOnce(): Promise<number> {
    const batch = this.ring.peekFrom(this.ackedSeq, BATCH_SIZE);
    if (batch.length === 0) return 0;

    const payload: ReportPayload = {
      category: "activity",
      instance_id: this.instanceId,
      events: batch.map(e => ({ ...e.payload, local_seq: e.localSeq })),
    };

    let result: unknown;
    try {
      result = await this.callToolWithTimeout(payload);
    } catch (err) {
      this.logger.warn(`emitter flush failed: ${(err as Error).message}`);
      await this.session.invalidate();
      return 0;
    }

    const raw = (result && typeof result === "object" ? result : {}) as
      ReportResponse & { structuredContent?: ReportResponse; isError?: boolean };

    // A tool-level error (isError, not a transport throw) means the batch was
    // NOT accepted — keep it and retry on the next tick. Do NOT fall through to
    // the forward-progress fallback below, which would evict undelivered events.
    if (raw.isError === true) {
      this.logger.warn("emitter flush: sidecar reported tool error; retaining batch");
      return 0;
    }

    // The sidecar's {accepted,last_local_seq} ack reaches us in one of three
    // shapes depending on the MCP SDK / tool output-schema: under
    // `.structuredContent` (mcp>=1.26 with a schema), as a flat field, or — for
    // a bare `@mcp.tool() -> dict` with no output schema — only as serialized
    // *text content* (unreadable here). In that last case last_local_seq is
    // undefined; leaving last=0 means the ring never evicts and every backoff
    // tick re-sends the same backlog forever (the event flood — a finished run's
    // chat_start kept reviving the "thinking" bubble). Mirror the hermes emitter:
    // on a successful call, fall back to the max local_seq we just sent so the
    // ring always makes forward progress.
    const response: ReportResponse =
      raw.structuredContent && typeof raw.structuredContent === "object"
        ? raw.structuredContent
        : raw;
    let last = typeof response.last_local_seq === "number" ? response.last_local_seq : 0;
    const accepted =
      typeof response.accepted === "number" ? response.accepted : batch.length;
    if (last === 0 && batch.length > 0) {
      last = batch.reduce((m, e) => Math.max(m, e.localSeq), 0);
    }
    if (last > 0) {
      this.ring.evictThrough(last);
      this.ackedSeq = last;
    }
    return accepted;
  }

  private callToolWithTimeout(payload: ReportPayload): Promise<unknown> {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new Error(`call_tool timeout after ${CALL_SIDECAR_TIMEOUT_MS}ms`)),
        CALL_SIDECAR_TIMEOUT_MS,
      );
      this.session.callTool("host_agent_event_report", payload as unknown as Record<string, unknown>)
        .then((v) => { clearTimeout(timer); resolve(v); })
        .catch((e) => { clearTimeout(timer); reject(e); });
    });
  }

  private waitWakeOrTimeout(timeoutMs: number): Promise<void> {
    return new Promise((resolve) => {
      const onWake = () => { clearTimeout(timer); resolve(); };
      const timer = setTimeout(() => { this.wake.off("wake", onWake); resolve(); }, timeoutMs);
      this.wake.once("wake", onWake);
    });
  }
}
