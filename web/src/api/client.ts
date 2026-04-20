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

// Auth-aware fetch. On 401 the first try, attempt a silent /auth/refresh and
// retry once. If refresh itself fails, dispatch an "auth:unauthenticated"
// event so the app can redirect to the login page. credentials: "include" is
// required so the httpOnly session cookies are sent.
let refreshInFlight: Promise<boolean> | null = null;

async function attemptRefresh(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        // 5s cap so a hung server never blocks all concurrent 401 retries.
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 5000);
        try {
          const r = await fetch(apiPath("/auth/refresh"), {
            method: "POST",
            credentials: "include",
            signal: controller.signal,
          });
          return r.ok;
        } finally {
          clearTimeout(timer);
        }
      } catch {
        return false;
      } finally {
        setTimeout(() => {
          refreshInFlight = null;
        }, 0);
      }
    })();
  }
  return refreshInFlight;
}

function notifyUnauthenticated() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("auth:unauthenticated"));
  }
}

async function rawFetch(path: string, init: RequestInit, query?: QueryParams): Promise<Response> {
  return fetch(apiPath(path, query), { ...init, credentials: "include" });
}

export async function apiRequest<T>(
  path: string,
  options: Omit<RequestInit, "body"> & {
    body?: unknown;
    query?: QueryParams;
    skipAuthRetry?: boolean;
  } = {},
): Promise<{ data: T; response: Response }> {
  const { body, headers, query, skipAuthRetry, ...init } = options;
  const resolvedHeaders = new Headers(headers);
  resolvedHeaders.set("Accept", "application/json");

  let payload: BodyInit | undefined;
  if (body !== undefined) {
    resolvedHeaders.set("Content-Type", "application/json");
    payload = JSON.stringify(body);
  }

  const fetchInit: RequestInit = {
    ...init,
    body: payload,
    headers: resolvedHeaders,
  };

  let response = await rawFetch(path, fetchInit, query);

  if (response.status === 401 && !skipAuthRetry) {
    const refreshed = await attemptRefresh();
    if (refreshed) {
      response = await rawFetch(path, fetchInit, query);
    }
    if (response.status === 401) {
      notifyUnauthenticated();
    }
  }

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
    skipAuthRetry?: boolean;
  } = {},
): Promise<T> {
  const { data } = await apiRequest<T>(path, options);
  return data;
}
