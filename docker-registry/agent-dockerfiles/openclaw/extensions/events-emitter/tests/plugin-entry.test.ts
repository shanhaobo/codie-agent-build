import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import entry from "../index.js";

const mkApi = (overrides: Partial<{
  config: Record<string, unknown>;
  envVar: string | undefined;
}> = {}) => {
  if (overrides.envVar === undefined) delete process.env.CODIE_INSTANCE_ID;
  else process.env.CODIE_INSTANCE_ID = overrides.envVar;

  return {
    config: overrides.config ?? {
      mcp: {
        servers: {
          codie_host: {
            url: "http://host.docker.internal:55141/mcp/mcp",
            headers: { Authorization: "Bearer tok-abc" },
          },
        },
      },
    },
    logger: { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() },
    agent: {
      events: { registerAgentEventSubscription: vi.fn() },
    },
    on: vi.fn(),
    lifecycle: { registerRuntimeLifecycle: vi.fn() },
  };
};

// Mock the openclaw plugin-sdk (we declared a stub for TS compile but at test
// time we want a real value to import).
vi.mock("openclaw/plugin-sdk/plugin-entry", () => ({
  definePluginEntry: (opts: unknown) => opts,
}));

// Mock the SDK so register() doesn't try to open a socket.
vi.mock("@modelcontextprotocol/sdk/client/index.js", () => ({
  Client: vi.fn().mockImplementation(() => ({
    connect: vi.fn(async () => {}),
    callTool: vi.fn(async () => ({ accepted: 0, last_local_seq: 0 })),
    close: vi.fn(async () => {}),
  })),
}));
vi.mock("@modelcontextprotocol/sdk/client/streamableHttp.js", () => ({
  StreamableHTTPClientTransport: vi.fn().mockImplementation(() => ({})),
}));

beforeEach(() => vi.clearAllMocks());
afterEach(() => { delete process.env.CODIE_INSTANCE_ID; });

describe("plugin entry", () => {
  it("registers stream subscription + 2 typed hooks + lifecycle when env+config present", () => {
    const api = mkApi({ envVar: "inst-xyz" });
    entry.register(api as never);

    expect(api.agent.events.registerAgentEventSubscription).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "codie-events-emitter",
        streams: ["lifecycle", "tool"],
        handle: expect.any(Function),
      }),
    );
    expect(api.on).toHaveBeenCalledWith("subagent_spawning", expect.any(Function));
    expect(api.on).toHaveBeenCalledWith("subagent_ended", expect.any(Function));
    expect(api.lifecycle.registerRuntimeLifecycle).toHaveBeenCalledWith(
      expect.objectContaining({ onStart: expect.any(Function), onStop: expect.any(Function) }),
    );
  });

  it("is inert (logs warn, registers nothing) when CODIE_INSTANCE_ID missing", () => {
    const api = mkApi({ envVar: undefined });
    entry.register(api as never);
    expect(api.logger.warn).toHaveBeenCalledWith(
      expect.stringContaining("CODIE_INSTANCE_ID"),
    );
    expect(api.agent.events.registerAgentEventSubscription).not.toHaveBeenCalled();
    expect(api.on).not.toHaveBeenCalled();
  });

  it("is inert when codie_host config missing", () => {
    const api = mkApi({ envVar: "inst-1", config: { mcp: {} } });
    entry.register(api as never);
    expect(api.logger.warn).toHaveBeenCalledWith(
      expect.stringContaining("codie_host"),
    );
    expect(api.agent.events.registerAgentEventSubscription).not.toHaveBeenCalled();
  });

  it("register completes when config is well-formed", () => {
    // The Bearer-stripping logic itself is exercised transitively when the handle
    // closure calls HostSession (covered in Task 8 e2e). Here we just confirm
    // register() does not throw with a typical well-formed config.
    const api = mkApi({ envVar: "inst-1" });
    expect(() => entry.register(api as never)).not.toThrow();
  });
});
