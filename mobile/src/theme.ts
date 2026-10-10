import { StyleSheet } from "react-native";

export const color = {
  basil: "#2F5D50",
  basilTint: "#E3ECE8",
  tomato: "#C8402F",
  tomatoTint: "#F8E5E1",
  oil: "#B48A12",
  ink: "#1E2421",
  muted: "#5F6A65",
  line: "#DDE2DF",
  surface: "#FFFFFF",
  page: "#F3F5F3",
  ticket: "#FFFEFA",
};

export const s = StyleSheet.create({
  page: { flex: 1, backgroundColor: color.page },
  pad: { padding: 16, gap: 12 },
  h1: { fontSize: 24, fontWeight: "700", color: color.ink },
  h2: { fontSize: 17, fontWeight: "600", color: color.ink },
  body: { fontSize: 15, color: color.ink, lineHeight: 21 },
  muted: { fontSize: 14, color: color.muted },
  error: { fontSize: 14, color: color.tomato },
  panel: { backgroundColor: color.surface, borderColor: color.line, borderWidth: 1, borderRadius: 10, padding: 14, gap: 8 },
  input: {
    backgroundColor: color.surface,
    borderColor: color.line,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 15,
    color: color.ink,
  },
  btn: { backgroundColor: color.basil, borderRadius: 8, paddingVertical: 11, paddingHorizontal: 16, alignItems: "center" },
  btnText: { color: "#fff", fontWeight: "600", fontSize: 15 },
  btnGhost: { borderColor: color.line, borderWidth: 1, borderRadius: 8, paddingVertical: 10, paddingHorizontal: 14, alignItems: "center" },
  btnGhostText: { color: color.ink, fontWeight: "600" },
  row: { flexDirection: "row", alignItems: "center", gap: 8 },
  spread: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  bigNumber: { fontSize: 30, fontWeight: "700", color: color.ink, fontVariant: ["tabular-nums"] },
});
