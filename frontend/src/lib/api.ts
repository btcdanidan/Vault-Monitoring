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
