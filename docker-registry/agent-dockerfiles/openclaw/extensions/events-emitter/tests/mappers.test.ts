import { describe, it, expect, vi, beforeEach } from "vitest";
import { mapLifecycle, mapTool, mapSubagentSpawning, mapSubagentEnded } from "../src/mappers.js";

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(1717000000000));
});

describe("mapLifecycle", () => {
  it("phase=start → chat_start", () => {
    const env = mapLifecycle({ runId: "r1", data: { phase: "start" } });
    expect(env).toEqual({
      type: "chat_start",
      run_id: "r1",
      ts_ms: 1717000000000,
      data: {},
    });
  });

  it("phase=end → chat_end with exit_reason=natural", () => {
    const env = mapLifecycle({ runId: "r2", data: { phase: "end" } });
    expect(env?.type).toBe("chat_end");
    expect(env?.data.exit_reason).toBe("natural");
  });

  it("phase=error → chat_end with exit_reason=error", () => {
    const env = mapLifecycle({ runId: "r3", data: { phase: "error" } });
    expect(env?.type).toBe("chat_end");
    expect(env?.data.exit_reason).toBe("error");
  });

  it("phase=fallback_step is filtered (returns null)", () => {
    expect(mapLifecycle({ runId: "r4", data: { phase: "fallback_step" } })).toBeNull();
    expect(mapLifecycle({ runId: "r4", data: { phase: "fallback" } })).toBeNull();
    expect(mapLifecycle({ runId: "r4", data: { phase: "fallback_cleared" } })).toBeNull();
  });

  it("missing or non-string phase returns null", () => {
    expect(mapLifecycle({ runId: "r5", data: {} })).toBeNull();
    expect(mapLifecycle({ runId: "r5", data: { phase: 42 } as unknown as { phase: string } })).toBeNull();
  });
});

describe("mapTool", () => {
  // OpenClaw's tool-stream event names the tool under data.name (not data.tool).
  it("phase=start → tool_start with tool name (from data.name) and call id", () => {
    const env = mapTool({
      runId: "r1",
      data: { phase: "start", name: "shell_exec", toolCallId: "tc-1" },
    });
    expect(env).toEqual({
      type: "tool_start",
      run_id: "r1",
      ts_ms: 1717000000000,
      data: { tool: "shell_exec", tool_call_id: "tc-1" },
    });
  });

  // Regression: the legacy data.tool key must NOT be read (it's always absent
  // on real events) — doing so produced the empty "tool" bubble label.
  it("ignores legacy data.tool; absent data.name → empty tool name", () => {
    const env = mapTool({
      runId: "r1",
      data: { phase: "start", tool: "shell_exec", toolCallId: "tc-1" },
    });
    expect(env?.data).toEqual({ tool: "", tool_call_id: "tc-1" });
  });

  it("phase=result → tool_end with is_error=false when no error", () => {
    const env = mapTool({
      runId: "r1",
      data: { phase: "result", name: "shell_exec", toolCallId: "tc-1" },
    });
    expect(env?.type).toBe("tool_end");
    expect(env?.data).toEqual({ tool: "shell_exec", tool_call_id: "tc-1", is_error: false });
  });

  it("phase=result with error → tool_end is_error=true", () => {
    const env = mapTool({
      runId: "r1",
      data: { phase: "result", name: "shell_exec", toolCallId: "tc-1", error: "boom" },
    });
    expect(env?.data.is_error).toBe(true);
  });

  it("phase=update is filtered (returns null)", () => {
    expect(mapTool({ runId: "r1", data: { phase: "update" } })).toBeNull();
  });

  it("unknown phases filtered", () => {
    expect(mapTool({ runId: "r1", data: { phase: "abandoned" } })).toBeNull();
    expect(mapTool({ runId: "r1", data: {} })).toBeNull();
  });

  it("phase=start with missing tool/toolCallId defaults to empty strings", () => {
    const env = mapTool({ runId: "r1", data: { phase: "start" } });
    expect(env?.type).toBe("tool_start");
    expect(env?.data).toEqual({ tool: "", tool_call_id: "" });
  });
});

describe("mapSubagent", () => {
  it("mapSubagentSpawning produces subagent_spawn envelope with real openclaw fields", () => {
    const env = mapSubagentSpawning({
      childSessionKey: "ck-1",
      agentId: "agent-a",
      label: "sub-a",
      mode: "delegate",
      requester: "user",
    });
    expect(env).toEqual({
      type: "subagent_spawn",
      run_id: "ck-1",
      ts_ms: 1717000000000,
      data: {
        child_session_key: "ck-1",
        agent_id: "agent-a",
        label: "sub-a",
        mode: "delegate",
        requester: "user",
      },
    });
  });

  it("mapSubagentEnded prefers runId when present", () => {
    const env = mapSubagentEnded({
      targetSessionKey: "tk-1",
      targetKind: "child",
      reason: "completed",
      runId: "r1",
      outcome: "failed",
    });
    expect(env).toEqual({
      type: "subagent_end",
      run_id: "r1",
      ts_ms: 1717000000000,
      data: {
        target_session_key: "tk-1",
        target_kind: "child",
        reason: "completed",
        outcome: "failed",
        success: false,
      },
    });
  });

  it("mapSubagentEnded falls back to targetSessionKey when runId missing", () => {
    const env = mapSubagentEnded({
      targetSessionKey: "tk-2",
      outcome: "ok",
    });
    expect(env.run_id).toBe("tk-2");
    expect(env.data.success).toBe(true);
  });

  it("mapSubagentSpawning with only required fields defaults optionals to empty strings", () => {
    const env = mapSubagentSpawning({ childSessionKey: "ck-3", agentId: "agent-b" });
    expect(env.data).toEqual({
      child_session_key: "ck-3",
      agent_id: "agent-b",
      label: "",
      mode: "",
      requester: "",
    });
  });
});
