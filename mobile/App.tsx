import { StatusBar } from "expo-status-bar";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { ActivityIndicator, Pressable, Text, View } from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import { api, getToken, signOut, supabase, type Me } from "./src/api";
import AlertsScreen from "./src/screens/AlertsScreen";
import AssistantScreen from "./src/screens/AssistantScreen";
import LoginScreen from "./src/screens/LoginScreen";
import SettingsScreen from "./src/screens/SettingsScreen";
import TodayScreen from "./src/screens/TodayScreen";
import { color, s } from "./src/theme";

type Tab = "assistant" | "today" | "alerts" | "settings";
const TABS: { id: Tab; label: string }[] = [
  { id: "assistant", label: "Assistant" },
  { id: "today", label: "Today" },
  { id: "alerts", label: "Alerts" },
  { id: "settings", label: "Settings" },
];

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [phase, setPhase] = useState<"loading" | "out" | "in" | "no-restaurant">("loading");
  const [tab, setTab] = useState<Tab>("assistant");

  const load = useCallback(async () => {
    if (!(await getToken())) {
      setPhase("out");
      return;
    }
    try {
      const data = await api<Me>("/me");
      setMe(data);
      setPhase(data.locations.length ? "in" : "no-restaurant");
    } catch {
      await signOut();
      setPhase("out");
    }
  }, []);

  useEffect(() => {
    void load();
    if (!supabase) return;
    const { data } = supabase.auth.onAuthStateChange(() => void load());
    return () => data.subscription.unsubscribe();
  }, [load]);

  let content: ReactNode;
  if (phase === "loading") content = <ActivityIndicator style={{ marginTop: 80 }} color={color.basil} />;
  else if (phase === "out") content = <LoginScreen onSignedIn={load} />;
  else if (phase === "no-restaurant" || !me)
    content = (
      <View style={s.pad}>
        <Text style={s.h1}>Finish setup on the web</Text>
        <Text style={s.body}>Create your restaurant and connect your POS in the web app, then come back here.</Text>
        <Pressable style={s.btnGhost} onPress={() => void load()}>
          <Text style={s.btnGhostText}>I've done that</Text>
        </Pressable>
      </View>
    );
  else {
    const location = me.locations[0];
    content = (
      <View style={{ flex: 1 }}>
        <View style={{ flex: 1 }}>
          {tab === "assistant" && <AssistantScreen location={location} />}
          {tab === "today" && <TodayScreen location={location} />}
          {tab === "alerts" && <AlertsScreen location={location} />}
          {tab === "settings" && (
            <SettingsScreen
              email={me.email}
              onSignOut={async () => {
                await signOut();
                setPhase("out");
              }}
            />
          )}
        </View>
        <View style={{ flexDirection: "row", borderTopWidth: 1, borderColor: color.line, backgroundColor: color.surface }}>
          {TABS.map((t) => (
            <Pressable key={t.id} onPress={() => setTab(t.id)} style={{ flex: 1, paddingVertical: 12, alignItems: "center" }}>
              <Text style={{ color: tab === t.id ? color.basil : color.muted, fontWeight: tab === t.id ? "700" : "500" }}>
                {t.label}
              </Text>
            </Pressable>
          ))}
        </View>
      </View>
    );
  }

  return (
    <SafeAreaProvider>
      <SafeAreaView style={s.page} edges={["top", "bottom"]}>
        <StatusBar style="dark" />
        {content}
      </SafeAreaView>
    </SafeAreaProvider>
  );
}
