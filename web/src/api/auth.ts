import { apiFetch } from "./client";

export interface AuthUser {
  username: string;
}

export async function login(username: string, password: string): Promise<AuthUser> {
  return apiFetch<AuthUser>("/auth/login", {
    method: "POST",
    body: { username, password },
    skipAuthRetry: true,
  });
}

export async function logout(): Promise<void> {
  await apiFetch<{ ok: boolean }>("/auth/logout", { method: "POST", skipAuthRetry: true });
}

export async function fetchMe(): Promise<AuthUser> {
  return apiFetch<AuthUser>("/auth/me", { skipAuthRetry: true });
}
