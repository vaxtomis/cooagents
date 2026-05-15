import { apiFetchText } from "./client";

// Returns raw, unsanitized Markdown for the Step3 context artifact of a
// DevWork round. Render only via MarkdownPanel / rehype-sanitize.
export async function getDevWorkContextContent(
  devWorkId: string,
  round: number,
): Promise<string> {
  return apiFetchText(
    `/dev-works/${encodeURIComponent(devWorkId)}/context/${encodeURIComponent(round)}/content`,
  );
}
