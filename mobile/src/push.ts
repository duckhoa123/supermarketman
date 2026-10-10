import Constants from "expo-constants";
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";
import { api } from "./api";

Notifications.setNotificationHandler({
  handleNotification: async () => ({ shouldShowAlert: true, shouldPlaySound: true, shouldSetBadge: true }),
});

/** Ask for permission, get this device's Expo push token and register it with the API. */
export async function registerForPush(): Promise<string> {
  if (!Device.isDevice) return "Push notifications need a real phone, not a simulator.";
  if (Platform.OS === "android") {
    await Notifications.setNotificationChannelAsync("alerts", {
      name: "Alerts",
      importance: Notifications.AndroidImportance.HIGH,
    });
  }
  const existing = await Notifications.getPermissionsAsync();
  let status = existing.status;
  if (status !== "granted") status = (await Notifications.requestPermissionsAsync()).status;
  if (status !== "granted") return "Notifications are turned off for Pass in your phone's settings.";
  const projectId = (Constants.expoConfig?.extra as { eas?: { projectId?: string } } | undefined)?.eas?.projectId;
  const token = (await Notifications.getExpoPushTokenAsync(projectId ? { projectId } : undefined)).data;
  await api("/me/push-subscriptions", { method: "POST", body: { kind: "expo", token } });
  return "Notifications are on.";
}
