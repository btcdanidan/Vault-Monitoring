/**
 * API client — single source for all backend endpoint calls.
 * Use with TanStack Query; validate responses with Zod.
 * Pass accessToken for authenticated requests (Bearer JWT to FastAPI).
 */

import { createClient } from "@/lib/supabase/client";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "/api";

export type ApiOptions = {
  accessToken?: string | null;
};

function headers(accessToken?: string | null): HeadersInit {
  const h: HeadersInit = {};
  if (accessToken) {
    (h as Record<string, string>)["Authorization"] = `Bearer ${accessToken}`;
  }
  return h;
}

/**
 * Returns the current session access token from the browser Supabase client.
 * Use when calling apiGet/apiPost from client components to pass JWT to FastAPI.
 */
export async function getSessionToken(): Promise<string | null> {
  const supabase = createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  return session?.access_token ?? null;
}

export async function apiGet<T>(
  path: string,
  options: ApiOptions = {}
): Promise<T> {
  const { accessToken } = options;
  const res = await fetch(`${API_BASE}${path}`, {
    headers: headers(accessToken),
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json() as Promise<T>;
}

export async function apiPost<T>(
  path: string,
  body: unknown,
  options: ApiOptions = {}
): Promise<T> {
  const { accessToken } = options;
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...headers(accessToken),
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json() as Promise<T>;
}

export async function apiDelete<T>(
  path: string,
  body?: unknown,
  options: ApiOptions = {}
): Promise<T> {
  const { accessToken } = options;
  const init: RequestInit = {
    method: "DELETE",
    headers: {
      ...headers(accessToken),
    },
  };
  if (body !== undefined) {
    (init.headers as Record<string, string>)["Content-Type"] =
      "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json() as Promise<T>;
}

/**
 * Low-level fetch that returns the raw Response (does not throw on non-2xx).
 * Use when callers need to inspect status codes (e.g., 403 routing on login).
 */
export async function apiFetchRaw(
  path: string,
  options: ApiOptions = {}
): Promise<Response> {
  const { accessToken } = options;
  return fetch(`${API_BASE}${path}`, {
    headers: headers(accessToken),
  });
}
