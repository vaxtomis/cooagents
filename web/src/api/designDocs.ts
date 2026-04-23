import type { DesignDoc, DesignDocStatus } from "../types";
import { apiFetch, apiFetchText } from "./client";

export async function listDesignDocs(
  workspaceId: string,
  status?: DesignDocStatus,
): Promise<DesignDoc[]> {
  return apiFetch<DesignDoc[]>("/design-docs", {
    query: { workspace_id: workspaceId, status },
  });
}

export async function getDesignDoc(id: string): Promise<DesignDoc> {
  return apiFetch<DesignDoc>(`/design-docs/${encodeURIComponent(id)}`);
}

// Returns raw, unsanitized Markdown. Content endpoint returns text/markdown.
// Callers MUST pass the result through a Markdown renderer with HTML
// sanitization (e.g. MarkdownPanel, which wires up rehype-sanitize). Do not
// inject the returned string via dangerouslySetInnerHTML or similar.
//
// Errors: 410 = file deleted on disk (UI should render reconcile hint);
// 404 = DesignDoc row missing; 400 = path escapes workspaces_root.
export async function getDesignDocContent(id: string): Promise<string> {
  return apiFetchText(`/design-docs/${encodeURIComponent(id)}/content`);
}
