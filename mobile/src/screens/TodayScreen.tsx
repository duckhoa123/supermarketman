import { useCallback, useEffect, useState } from "react";
import { RefreshControl, ScrollView, Text, View } from "react-native";
import { api, type Location } from "../api";
import { color, s } from "../theme";

type Dashboard = {
  summary: {
    gross: string;
    orders: number;
    avg_ticket: string;
    note?: string;
    comparison?: { gross_change_pct: number | null };
  };
  top_items: { rows: { name: string; qty: number; revenue: string }[] };
  low_stock: { items: { id: string; name: string; stock_qty: number | null; unit: string | null }[] };
};

export default function TodayScreen({ location }: { location: Location }) {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      setData(await api<Dashboard>(`/locations/${location.id}/dashboard?period=today`));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't load.");
    } finally {
      setRefreshing(false);
    }
  }, [location.id]);

  useEffect(() => {
    void load();
  }, [load]);

  const change = data?.summary.comparison?.gross_change_pct;

  return (
    <ScrollView contentContainerStyle={s.pad} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={load} />}>
      <Text style={s.h1}>Today</Text>
      {error ? <Text style={s.error}>{error}</Text> : null}
      {data && (
        <>
          <View style={s.panel}>
            <Text style={s.muted}>Sales so far</Text>
            <Text style={s.bigNumber}>{data.summary.gross}</Text>
            {change !== null && change !== undefined ? (
              <Text style={{ color: change >= 0 ? color.basil : color.tomato, fontWeight: "600" }}>
                {change > 0 ? "+" : ""}
                {change.toFixed(0)}% vs last week
              </Text>
            ) : null}
            <Text style={s.muted}>
              {data.summary.orders} orders · average {data.summary.avg_ticket}
            </Text>
          </View>
          <View style={s.panel}>
            <Text style={s.h2}>Best sellers</Text>
            {data.top_items.rows.map((r) => (
              <View key={r.name} style={s.spread}>
                <Text style={s.body}>{r.name}</Text>
                <Text style={s.muted}>{r.qty} sold</Text>
              </View>
            ))}
          </View>
          <View style={s.panel}>
            <Text style={s.h2}>Running low</Text>
            {data.low_stock.items.length === 0 ? <Text style={s.muted}>Nothing below its alert level.</Text> : null}
            {data.low_stock.items.map((i) => (
              <View key={i.id} style={s.spread}>
                <Text style={s.body}>{i.name}</Text>
                <Text style={{ color: color.tomato, fontWeight: "600" }}>
                  {i.stock_qty} {i.unit ?? "left"}
                </Text>
              </View>
            ))}
          </View>
        </>
      )}
    </ScrollView>
  );
}
