export class ApiError extends Error {
  status: number;
  data: unknown;

  constructor(status: number, message: string, data: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

export type QueryValue = string | number | boolean | null | undefined;
export type QueryParams = Record<string, QueryValue | QueryValue[]>;

const API_PREFIX = "/api/v1";

function appendQuery(searchParams: URLSearchParams, key: string, value: QueryValue | QueryValue[]) {
  if (Array.isArray(value)) {
    value.forEach((entry) => appendQuery(searchParams, key, entry));
    return;
  }

  if (value === undefined || value === null || value === "") {
    return;
  }

  searchParams.append(key, String(value));
}

export function apiPath(path: string, query?: QueryParams): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const url = new URL(`${API_PREFIX}${normalizedPath}`, "http://localhost");

  if (query) {
    Object.entries(query).forEach(([key, value]) => appendQuery(url.searchParams, key, value));
  }

  const search = url.searchParams.toString();
  return search ? `${url.pathname}?${search}` : url.pathname;
}

async function parseResponseBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    return response.json();
  }

  const text = await response.text();
  return text.length > 0 ? text : null;
}

export async function apiRequest<T>(
  path: string,
  options: Omit<RequestInit, "body"> & {
    body?: unknown;
    query?: QueryParams;
  } = {},
): Promise<{ data: T; response: Response }> {
  const { body, headers, query, ...init } = options;
  const resolvedHeaders = new Headers(headers);
  resolvedHeaders.set("Accept", "application/json");

  let payload: BodyInit | undefined;
  if (body !== undefined) {
    resolvedHeaders.set("Content-Type", "application/json");
    payload = JSON.stringify(body);
  }

  const response = await fetch(apiPath(path, query), {
    ...init,
    body: payload,
    headers: resolvedHeaders,
  });

  const data = await parseResponseBody(response);

  if (!response.ok) {
    const message =
      typeof data === "object" && data !== null && "message" in data
        ? String((data as { message?: unknown }).message)
        : `API request failed with status ${response.status}`;
    throw new ApiError(response.status, message, data);
  }

  return { data: data as T, response };
}

export async function apiFetch<T>(
  path: string,
  options: Omit<RequestInit, "body"> & {
    body?: unknown;
    query?: QueryParams;
  } = {},
): Promise<T> {
  const { data } = await apiRequest<T>(path, options);
  return data;
}
