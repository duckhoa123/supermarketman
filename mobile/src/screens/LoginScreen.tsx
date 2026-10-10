import { useState } from "react";
import { Pressable, Text, TextInput, View } from "react-native";
import { devMode, signIn } from "../api";
import { s } from "../theme";

export default function LoginScreen({ onSignedIn }: { onSignedIn: () => Promise<void> }) {
  const [email, setEmail] = useState(devMode ? "owner@example.com" : "");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  async function submit() {
    setError("");
    try {
      const msg = await signIn(email);
      if (msg) setNotice(msg);
      else await onSignedIn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sign-in failed.");
    }
  }

  return (
    <View style={[s.pad, { marginTop: 48 }]}>
      <Text style={s.h1}>Your restaurant, one question away</Text>
      <Text style={s.muted}>Sales, stock and reviews from your POS, and alerts when something needs you.</Text>
      <TextInput
        style={s.input}
        value={email}
        onChangeText={setEmail}
        placeholder="Work email"
        autoCapitalize="none"
        keyboardType="email-address"
        autoComplete="email"
      />
      <Pressable style={s.btn} onPress={() => void submit()}>
        <Text style={s.btnText}>{devMode ? "Sign in (development)" : "Email me a sign-in link"}</Text>
      </Pressable>
      {notice ? <Text style={s.body}>{notice}</Text> : null}
      {error ? <Text style={s.error}>{error}</Text> : null}
    </View>
  );
}
