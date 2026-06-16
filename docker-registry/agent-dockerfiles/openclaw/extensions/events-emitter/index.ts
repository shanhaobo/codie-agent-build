import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { HostSession } from "./src/host-session.js";
import { CodieEmitter } from "./src/emitter.js";
import {
  mapLifecycle,
  mapTool,
  mapSubagentSpawning,
  mapSubagentEnded,
  type AgentEventLike,
  type SubagentSpawnCtx,
  type SubagentEndCtx,
} from "./src/mappers.js";

// Plugin entry id MUST match the manifest id (openclaw.plugin.json) and the
// dist directory name, else the loader rejects with "plugin id mismatch".
const PLUGIN_ID = "events-emitter";
// Agent-event subscription id is an internal handle; kept distinct/namespaced.
const SUBSCRIPTION_ID = "codie-events-emitter";

export default definePluginEntry({
  id: PLUGIN_ID,
  name: "Codie Events Emitter",
  description: "Forwards OpenClaw boundary events (lifecycle/tool/subagent) to codie-host-mcp for AgentDesk visualization.",
  register(api) {
    const apiAny = api as {
      logger: { debug: (m: string) => void; info: (m: string) => void; warn: (m: string) => void; error: (m: string) => void };
      config: { mcp?: { servers?: { codie_host?: { url?: unknown; headers?: { Authorization?: unknown } } } } };
      agent: { events: { registerAgentEventSubscription: (opts: { id: string; streams: string[]; handle: (event: { stream: string; runId: string; data: Record<string, unknown> }) => void }) => void } };
      on: (hookName: string, handler: (ctx: unknown) => void) => void;
      lifecycle: { registerRuntimeLifecycle: (lifecycle: { id: string; description?: string; cleanup?: () => void | Promise<void> }) => void };
    };

    const instanceId = process.env.CODIE_INSTANCE_ID;
    if (!instanceId) {
      apiAny.logger.warn(
        "[codie-events-emitter] CODIE_INSTANCE_ID env var not set — emitter inert. Bridge container_service.dart should inject this.",
      );
      return;
    }

    const codieHostCfg = apiAny.config?.mcp?.servers?.codie_host;
    const url = typeof codieHostCfg?.url === "string" ? codieHostCfg.url : undefined;
    const authHeader = typeof codieHostCfg?.headers?.Authorization === "string"
      ? codieHostCfg.headers.Authorization
      : undefined;
    if (!url || !authHeader) {
      apiAny.logger.warn(
        "[codie-events-emitter] mcp.servers.codie_host missing url or headers.Authorization — emitter inert. Check openclaw_manifest.dart codie_host injection.",
      );
      return;
    }
    const token = authHeader.replace(/^Bearer\s+/i, "");

    const session = new HostSession(url, token);
    const emitter = new CodieEmitter(instanceId, session, apiAny.logger);

    apiAny.agent.events.registerAgentEventSubscription({
      id: SUBSCRIPTION_ID,
      streams: ["lifecycle", "tool"],
      handle: (event) => {
        const ev: AgentEventLike = { runId: event.runId, data: event.data };
        const envelope = event.stream === "lifecycle" ? mapLifecycle(ev)
          : event.stream === "tool" ? mapTool(ev)
          : null;
        if (envelope) emitter.emit(envelope);
      },
    });

    apiAny.on("subagent_spawning", (ctx) => {
      emitter.emit(mapSubagentSpawning(ctx as SubagentSpawnCtx));
    });

    apiAny.on("subagent_ended", (ctx) => {
      emitter.emit(mapSubagentEnded(ctx as SubagentEndCtx));
    });

    // OpenClaw 2026.5.x runtime lifecycle is { id, description?, cleanup? } — there
    // is no onStart/onStop. register() runs at gateway startup, so start the flush
    // loop here directly and wire stop() into cleanup (fires on shutdown).
    void emitter.start();
    apiAny.lifecycle.registerRuntimeLifecycle({
      id: PLUGIN_ID,
      description: "Codie events emitter flush loop",
      cleanup: () => emitter.stop(),
    });

    apiAny.logger.info(
      `[codie-events-emitter] registered: instance=${instanceId}, streams=[lifecycle,tool], hooks=[subagent_spawning,subagent_ended]`,
    );
  },
});
