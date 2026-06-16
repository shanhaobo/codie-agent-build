// Frozen contract per spec §2.1 — 8 hook names. Phase 2 ships 6 (subagent + chat + tool);
// permission_* deferred per spec §5.4 / Hermes-parity.
export type HookName =
  | "chat_start"
  | "chat_end"
  | "tool_start"
  | "tool_end"
  | "subagent_spawn"
  | "subagent_end"
  | "permission_wait"
  | "permission_resume";

// Frozen contract per spec §2.2.
export interface Envelope {
  type: HookName;
  run_id: string;
  ts_ms: number;
  data: Record<string, unknown>;
}

// Sent over MCP per spec §2.3.
export interface ReportPayload {
  category: "activity";
  instance_id: string;
  events: Array<Envelope & { local_seq: number }>;
}

// Returned by sidecar per spec §2.4.
export interface ReportResponse {
  last_local_seq?: number;
  accepted?: number;
}
