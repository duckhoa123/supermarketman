import { useState } from "react";
import { Pressable, ScrollView, Text } from "react-native";
import { registerForPush } from "../push";
import { s } from "../theme";

export default function SettingsScreen({ email, onSignOut }: { email: string; onSignOut: () => Promise<void> }) {
  const [notice, setNotice] = useState("");

  return (
    <ScrollView contentContainerStyle={s.pad}>
      <Text style={s.h1}>Settings</Text>
      <Text style={s.muted}>Signed in as {email}</Text>
      <Pressable
        style={s.btn}
        onPress={() =>
          void registerForPush()
            .then(setNotice)
            .catch((e: unknown) => setNotice(e instanceof Error ? e.message : "Couldn't turn on notifications."))
        }
      >
        <Text style={s.btnText}>Turn on notifications</Text>
      </Pressable>
      {notice ? <Text style={s.body}>{notice}</Text> : null}
      <Text style={s.muted}>
        Connect your POS and Google reviews, and choose which alerts you get, in Settings on the web app.
      </Text>
      <Pressable style={s.btnGhost} onPress={() => void onSignOut()}>
        <Text style={s.btnGhostText}>Sign out</Text>
      </Pressable>
    </ScrollView>
  );
}
