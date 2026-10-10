import { useRef, useState } from "react";
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, Text, TextInput, View } from "react-native";
import { chat, type Location } from "../api";
import { color, s } from "../theme";

type Turn = { role: "user" | "ai"; text: string; tools?: string[]; proposals?: string[] };

const STARTERS = ["How did we go yesterday?", "What's running low?", "Any bad reviews this week?"];

export default function AssistantScreen({ location }: { location: Location }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const conversationId = useRef<string | undefined>(undefined);
  const scroll = useRef<ScrollView>(null);

  async function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    setInput("");
    setBusy(true);
    setTurns((t) => [...t, { role: "user", text: message }]);
    try {
      const events = await chat(location.id, { message, conversation_id: conversationId.current });
      let answer = "";
      const tools: string[] = [];
      const proposals: string[] = [];
      for (const e of events) {
        if (e.type === "meta") conversationId.current = String(e.conversation_id);
        else if (e.type === "text") answer += String(e.delta);
        else if (e.type === "tool") tools.push(String(e.name));
        else if (e.type === "action") proposals.push(String((e.proposal as { label?: string }).label ?? ""));
        else if (e.type === "error") answer = String(e.message);
      }
      setTurns((t) => [...t, { role: "ai", text: answer, tools, proposals }]);
    } catch (e) {
      setTurns((t) => [...t, { role: "ai", text: e instanceof Error ? e.message : "Something went wrong." }]);
    } finally {
      setBusy(false);
      setTimeout(() => scroll.current?.scrollToEnd({ animated: true }), 50);
    }
  }

  return (
    <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <ScrollView ref={scroll} contentContainerStyle={s.pad}>
        <Text style={s.h1}>Ask about {location.name}</Text>
        {turns.length === 0 &&
          STARTERS.map((q) => (
            <Pressable key={q} style={s.panel} onPress={() => void send(q)}>
              <Text style={s.body}>{q}</Text>
            </Pressable>
          ))}
        {turns.map((t, i) =>
          t.role === "user" ? (
            <View key={i} style={{ alignSelf: "flex-end", backgroundColor: color.basil, borderRadius: 14, padding: 10, maxWidth: "85%" }}>
              <Text style={{ color: "#fff", fontSize: 15 }}>{t.text}</Text>
            </View>
          ) : (
            <View key={i} style={{ gap: 6 }}>
              {t.tools && t.tools.length > 0 ? <Text style={s.muted}>Checked: {t.tools.join(", ")}</Text> : null}
              <Text style={s.body}>{t.text}</Text>
              {(t.proposals ?? []).map((p, j) => (
                <View key={j} style={[s.panel, { backgroundColor: color.ticket, borderStyle: "dashed" }]}>
                  <Text style={s.h2}>{p}</Text>
                  <Text style={s.muted}>Approve this in the web app.</Text>
                </View>
              ))}
            </View>
          )
        )}
        {busy ? <Text style={s.muted}>Looking into it…</Text> : null}
      </ScrollView>
      <View style={[s.row, { padding: 12, borderTopWidth: 1, borderColor: color.line, backgroundColor: color.surface }]}>
        <TextInput
          style={[s.input, { flex: 1 }]}
          value={input}
          onChangeText={setInput}
          placeholder="Ask about sales, stock or reviews"
          onSubmitEditing={() => void send(input)}
          returnKeyType="send"
        />
        <Pressable style={s.btn} onPress={() => void send(input)} disabled={busy}>
          <Text style={s.btnText}>Ask</Text>
        </Pressable>
      </View>
    </KeyboardAvoidingView>
  );
}
