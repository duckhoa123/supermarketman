import "react-native-url-polyfill/auto";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import Constants from "expo-constants";

type Extra = { apiUrl?: string; supabaseUrl?: string; supabaseAnonKey?: string };
const extra = (Constants.expoConfig?.extra ?? {}) as Extra;

export const API_URL = (extra.apiUrl || "http://localhost:8000").trim().replace(/\/+$/, "");
export const supabase: SupabaseClient | null =
  extra.supabaseUrl && extra.supabaseAnonKey
    ? createClient(extra.supabaseUrl, extra.supabaseAnonKey, {
        auth: { storage: AsyncStorage, autoRefreshToken: true, persistSession: true, detectSessionInUrl: false },
      })
    : null;
export const devMode = supabase === null;
const DEV_KEY = "pass.devEmail";

export async function getToken(): Promise<string | null> {
  if (supabase) {
    const { data } = await supabase.auth.getSession();
    return data.session?.access_token ?? null;
  }
  const email = await AsyncStorage.getItem(DEV_KEY);
  return email ? `dev:${email}` : null;
}

export async function signIn(email: string): Promise<string> {
  if (supabase) {
    const { error } = await supabase.auth.signInWithOtp({ email });
    if (error) throw new Error(error.message);
    return "Check your email for a sign-in link, then come back to the app.";
  }
  await AsyncStorage.setItem(DEV_KEY, email.trim().toLowerCase());
  return "";
}

export async function signOut(): Promise<void> {
  if (supabase) await supabase.auth.signOut();
  await AsyncStorage.removeItem(DEV_KEY);
}

export async function api<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const token = await getToken();
  const res = await fetch(`${API_URL}${path}`, {
    method: init.method ?? "GET",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") message = data.detail;
    } catch {
      // not JSON
    }
    const err = new Error(message) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return (await res.json()) as T;
}

export type ChatEvent = { type: string; [key: string]: unknown };

/** React Native's fetch can't stream, so read the whole event stream and return the events. */
export async function chat(
  locationId: string,
  body: { message: string; conversation_id?: string; deep?: boolean }
): Promise<ChatEvent[]> {
  const token = await getToken();
  const res = await fetch(`${API_URL}/locations/${locationId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Request failed (${res.status})`);
  const text = await res.text();
  return text
    .split("\n")
    .filter((line) => line.startsWith("data: "))
    .map((line) => JSON.parse(line.slice(6)) as ChatEvent);
}

export type Location = { id: string; name: string; currency: string; timezone: string };
export type Me = { id: string; email: string; locations: Location[] };
