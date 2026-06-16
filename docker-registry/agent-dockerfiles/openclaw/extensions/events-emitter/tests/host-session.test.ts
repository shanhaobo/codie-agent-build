import { describe, it, expect, vi, beforeEach } from "vitest";
import { HostSession } from "../src/host-session.js";

const mockConnect = vi.fn(async () => {});
const mockCallTool = vi.fn(async (_args: unknown) => ({ content: [] }));
const mockClose = vi.fn(async () => {});

vi.mock("@modelcontextprotocol/sdk/client/index.js", () => ({
  Client: vi.fn().mockImplementation(() => ({
    connect: mockConnect,
    callTool: mockCallTool,
    close: mockClose,
  })),
}));

vi.mock("@modelcontextprotocol/sdk/client/streamableHttp.js", () => ({
  StreamableHTTPClientTransport: vi.fn().mockImplementation(() => ({})),
}));

beforeEach(() => {
  mockConnect.mockClear();
  mockCallTool.mockClear();
  mockClose.mockClear();
});

describe("HostSession", () => {
  it("connects lazily on first callTool", async () => {
    const s = new HostSession("http://h:8080/mcp/mcp", "tk");
    expect(mockConnect).not.toHaveBeenCalled();
    await s.callTool("foo", { a: 1 });
    expect(mockConnect).toHaveBeenCalledTimes(1);
    expect(mockCallTool).toHaveBeenCalledWith({ name: "foo", arguments: { a: 1 } });
  });

  it("reuses connection across multiple callTool", async () => {
    const s = new HostSession("http://h:8080/mcp/mcp", "tk");
    await s.callTool("a", {});
    await s.callTool("b", {});
    expect(mockConnect).toHaveBeenCalledTimes(1);
    expect(mockCallTool).toHaveBeenCalledTimes(2);
  });

  it("invalidate closes client and reconnects on next call", async () => {
    const s = new HostSession("http://h:8080/mcp/mcp", "tk");
    await s.callTool("a", {});
    await s.invalidate();
    expect(mockClose).toHaveBeenCalledTimes(1);
    await s.callTool("b", {});
    expect(mockConnect).toHaveBeenCalledTimes(2);
  });

  it("dedupes concurrent connects (stampede guard)", async () => {
    let resolve: () => void;
    mockConnect.mockImplementationOnce(
      () => new Promise<void>(r => { resolve = () => r(); }),
    );
    const s = new HostSession("http://h:8080/mcp/mcp", "tk");
    const [p1, p2, p3] = [s.callTool("a", {}), s.callTool("b", {}), s.callTool("c", {})];
    resolve!();
    await Promise.all([p1, p2, p3]);
    expect(mockConnect).toHaveBeenCalledTimes(1);
  });
});
