import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

export class HostSession {
  private client: Client | null = null;
  private connecting: Promise<void> | null = null;

  constructor(
    private readonly url: string,
    private readonly token: string,
  ) {}

  async callTool(name: string, args: Record<string, unknown>): Promise<unknown> {
    await this.ensureConnected();
    return this.client!.callTool({ name, arguments: args });
  }

  async invalidate(): Promise<void> {
    if (this.client) {
      try { await this.client.close(); } catch { /* swallow — already broken */ }
      this.client = null;
    }
    this.connecting = null;
  }

  private async ensureConnected(): Promise<void> {
    if (this.client) return;
    if (!this.connecting) {
      this.connecting = this.connect();
    }
    await this.connecting;
  }

  private async connect(): Promise<void> {
    const transport = new StreamableHTTPClientTransport(new URL(this.url), {
      requestInit: { headers: { Authorization: `Bearer ${this.token}` } },
    });
    const client = new Client({ name: "codie-openclaw-events-emitter", version: "0.1.0" }, {});
    await client.connect(transport);
    this.client = client;
  }
}
