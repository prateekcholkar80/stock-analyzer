export const VOICE_PREFERENCE_STORAGE_KEY = "jarvis-voice-enabled";

/**
 * Parses a stored voice-preference value. Corrupt or missing values fall
 * back to false (voice replies default to off) rather than blocking
 * rendering -- mirrors the chart-overlay preference discipline.
 */
export function readVoicePreference(rawValue: string | null): boolean {
  if (rawValue === null) return false;
  try {
    return JSON.parse(rawValue) === true;
  } catch {
    return false;
  }
}

/**
 * Whether a fresh spoken_message should trigger a speech-synthesis
 * fetch: voice must be enabled and the message must be real, non-blank
 * text.
 */
export function shouldPlaySpokenMessage(
  voiceEnabled: boolean,
  message: string | null | undefined,
): boolean {
  return voiceEnabled && Boolean(message && message.trim());
}
