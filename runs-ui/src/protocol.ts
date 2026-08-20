export const PROTOCOL_VERSION = 1;
export const EVENT_SCHEMA_VERSION = 1;
export const PROTOCOL_HEADER = "X-Jarvis-Protocol-Version";

export function validateEventVersion(event: {
  schema_version?: number;
}): void {
  // Version-less events remain readable during the protocol-v1 migration.
  const version = event.schema_version ?? EVENT_SCHEMA_VERSION;
  if (version !== EVENT_SCHEMA_VERSION) {
    throw new Error(
      `Unsupported event schema ${version}; Runs UI supports ` +
        `${EVENT_SCHEMA_VERSION}. Upgrade the Runs UI.`,
    );
  }
}
