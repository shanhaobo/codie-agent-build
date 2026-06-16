import type { Envelope } from "./envelope.js";

// Minimal shape we read from OpenClaw agent events; the real type ships in
// openclaw's plugin SDK but we only need runId + data.phase here.
export interface AgentEventLike {
  runId: string;
  data: Record<string, unknown>;
}

// Real shape from openclaw hook-types.ts:582-596 (PluginHookSubagentSpawningEvent)
export interface SubagentSpawnCtx {
  childSessionKey: string;
  agentId: string;
  label?: string;
  mode?: string;
  requester?: string;
  threadRequested?: boolean;
}

// Real shape from openclaw hook-types.ts:656-666 (PluginHookSubagentEndedEvent)
export interface SubagentEndCtx {
  targetSessionKey: string;
  targetKind?: string;
  reason?: string;
  runId?: string;
  endedAt?: number;
  outcome?: string; // "ok" | various failure literals
  error?: string;
}

const LIFECYCLE_WHITELIST = new Set(["start", "end", "error"]);
const TOOL_WHITELIST = new Set(["start", "result"]);

function getPhase(data: Record<string, unknown>): string | null {
  const p = data["phase"];
  return typeof p === "string" ? p : null;
}

export function mapLifecycle(event: AgentEventLike): Envelope | null {
  const phase = getPhase(event.data);
  if (!phase || !LIFECYCLE_WHITELIST.has(phase)) return null;
  if (phase === "start") {
    return { type: "chat_start", run_id: event.runId, ts_ms: Date.now(), data: {} };
  }
  return {
    type: "chat_end",
    run_id: event.runId,
    ts_ms: Date.now(),
    data: { exit_reason: phase === "error" ? "error" : "natural" },
  };
}

export function mapTool(event: AgentEventLike): Envelope | null {
  const phase = getPhase(event.data);
  if (!phase || !TOOL_WHITELIST.has(phase)) return null;
  // OpenClaw's tool-stream event carries the tool name under data.name (see
  // openclaw ui/src/ui/app-tool-stream.ts) — reading data.tool always yielded
  // "" so AgentDesk fell back to the generic "tool" label. The emitted envelope
  // keeps its `tool` key; the AgentDesk mapper accepts name/tool_name/tool.
  const tool = typeof event.data["name"] === "string" ? (event.data["name"] as string) : "";
  const toolCallId = typeof event.data["toolCallId"] === "string" ? (event.data["toolCallId"] as string) : "";
  if (phase === "start") {
    return {
      type: "tool_start",
      run_id: event.runId,
      ts_ms: Date.now(),
      data: { tool, tool_call_id: toolCallId },
    };
  }
  return {
    type: "tool_end",
    run_id: event.runId,
    ts_ms: Date.now(),
    data: {
      tool,
      tool_call_id: toolCallId,
      is_error: event.data["error"] != null,
    },
  };
}

export function mapSubagentSpawning(ctx: SubagentSpawnCtx): Envelope {
  return {
    type: "subagent_spawn",
    // No parent runId in spawn event; childSessionKey is the closest stable id for the new run.
    run_id: ctx.childSessionKey,
    ts_ms: Date.now(),
    data: {
      child_session_key: ctx.childSessionKey,
      agent_id: ctx.agentId,
      label: ctx.label ?? "",
      mode: ctx.mode ?? "",
      requester: ctx.requester ?? "",
    },
  };
}

export function mapSubagentEnded(ctx: SubagentEndCtx): Envelope {
  return {
    type: "subagent_end",
    // Prefer runId if openclaw provided it; else fall back to targetSessionKey.
    run_id: ctx.runId ?? ctx.targetSessionKey,
    ts_ms: Date.now(),
    data: {
      target_session_key: ctx.targetSessionKey,
      target_kind: ctx.targetKind ?? "",
      reason: ctx.reason ?? "",
      outcome: ctx.outcome ?? "",
      success: ctx.outcome === "ok",
    },
  };
}
