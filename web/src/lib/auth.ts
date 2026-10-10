// Sign-in: Supabase Auth in production; a dev-only email sign-in when VITE_SUPABASE_URL is not set.
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined;

export const supabase: SupabaseClient | null =
  SUPABASE_URL && SUPABASE_ANON_KEY ? createClient(SUPABASE_URL, SUPABASE_ANON_KEY) : null;

export const devMode = supabase === null;
const DEV_KEY = "pass.devEmail";

export async function getToken(): Promise<string | null> {
  if (supabase) {
    const { data } = await supabase.auth.getSession();
    return data.session?.access_token ?? null;
  }
  const email = localStorageGet(DEV_KEY);
  return email ? `dev:${email}` : null;
}

export async function signInWithEmail(email: string): Promise<string> {
  if (supabase) {
    const { error } = await supabase.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: window.location.origin },
    });
    if (error) throw new Error(error.message);
    return "Check your email for a sign-in link.";
  }
  localStorageSet(DEV_KEY, email.trim().toLowerCase());
  return "";
}

export async function signInWithGoogle(): Promise<void> {
  if (!supabase) return;
  await supabase.auth.signInWithOAuth({ provider: "google", options: { redirectTo: window.location.origin } });
}

export async function signOut(): Promise<void> {
  if (supabase) await supabase.auth.signOut();
  localStorageRemove(DEV_KEY);
}

export function onAuthChange(cb: () => void): () => void {
  if (!supabase) return () => undefined;
  const { data } = supabase.auth.onAuthStateChange(() => cb());
  return () => data.subscription.unsubscribe();
}

function localStorageGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}
function localStorageSet(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* storage unavailable: dev sign-in lasts for this tab only */
  }
}
function localStorageRemove(key: string) {
  try {
    window.localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}
