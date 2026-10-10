import { useCallback, useEffect, useState } from "react";
import { RefreshControl, ScrollView, Text, View } from "react-native";
import { api, type Location } from "../api";
import { s } from "../theme";

type AlertRow = { id: string; kind: string; title: string; body: string; created_at: string; read: boolean };

export default function AlertsScreen({ location }: { location: Location }) {
  const [alerts, setAlerts] = useState<AlertRow[]>([]);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const r = await api<{ alerts: AlertRow[]; unread: number }>(`/locations/${location.id}/alerts`);
      setAlerts(r.alerts);
      if (r.unread > 0) await api(`/locations/${location.id}/alerts/read-all`, { method: "POST" });
    } finally {
      setRefreshing(false);
    }
  }, [location.id]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <ScrollView contentContainerStyle={s.pad} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={load} />}>
      <Text style={s.h1}>Alerts</Text>
      {alerts.length === 0 && !refreshing ? <Text style={s.muted}>You're all caught up.</Text> : null}
      {alerts.map((a) => (
        <View key={a.id} style={s.panel}>
          <Text style={[s.h2, { fontWeight: a.read ? "500" : "700" }]}>{a.title}</Text>
          <Text style={s.body}>{a.body}</Text>
          <Text style={s.muted}>{new Date(a.created_at).toLocaleString()}</Text>
        </View>
      ))}
    </ScrollView>
  );
}
