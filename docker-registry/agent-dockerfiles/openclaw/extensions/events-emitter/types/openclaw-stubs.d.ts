// Minimal type declarations for openclaw modules used by the emitter.
// At runtime inside the OpenClaw docker image, the real types come from
// the workspace; for local TDD we mock these at test time and use these
// stubs only for TS compilation.

declare module "openclaw/plugin-sdk/plugin-entry" {
  export interface DefinedPluginEntry {
    readonly id: string;
    readonly name: string;
    readonly description: string;
    readonly register: (api: unknown) => void;
  }
  export function definePluginEntry<T extends {
    id: string;
    name: string;
    description: string;
    register: (api: unknown) => void;
  }>(opts: T): T;
}
