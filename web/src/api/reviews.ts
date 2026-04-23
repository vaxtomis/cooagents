import type { Review } from "../types";
import { apiFetch } from "./client";

export type ListReviewsParams =
  | { dev_work_id: string; design_work_id?: never }
  | { dev_work_id?: never; design_work_id: string };

// Exactly one filter is required — the backend rejects both/neither with 400.
// Returns rows ordered `round ASC, created_at ASC`.
export async function listReviews(params: ListReviewsParams): Promise<Review[]> {
  // Defence-in-depth: catch widening type drift before it hits the wire.
  const looseParams = params as { dev_work_id?: string; design_work_id?: string };
  if (looseParams.dev_work_id && looseParams.design_work_id) {
    throw new Error("listReviews: pass either dev_work_id or design_work_id, not both");
  }
  if (!looseParams.dev_work_id && !looseParams.design_work_id) {
    throw new Error("listReviews: dev_work_id or design_work_id is required");
  }
  return apiFetch<Review[]>("/reviews", { query: { ...params } });
}
