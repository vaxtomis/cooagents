import type { DevIterationNote } from "../types";
import { apiFetch, apiFetchText } from "./client";

// Returns notes ordered round ASC. UI consumers wanting reverse-chrono call
// .toReversed() on the result — the wire contract is ascending.
export async function listIterationNotes(devWorkId: string): Promise<DevIterationNote[]> {
  return apiFetch<DevIterationNote[]>(
    `/dev-works/${encodeURIComponent(devWorkId)}/iteration-notes`,
  );
}

// Returns raw, unsanitized Markdown — render only via a sanitizing pipeline
// (see MarkdownPanel / rehype-sanitize). Same 404/410/400 semantics as
// design-doc content.
export async function getIterationNoteContent(id: string): Promise<string> {
  return apiFetchText(`/dev-iteration-notes/${encodeURIComponent(id)}/content`);
}
