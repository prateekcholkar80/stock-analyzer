export const VOICE_INPUT_PREFERENCE_STORAGE_KEY = "jarvis-voice-input-enabled";

/**
 * Parses a stored voice-input-preference value. Corrupt or missing values
 * fall back to false (mic capture defaults to off) rather than blocking
 * rendering -- mirrors readVoicePreference in lib/voice.ts.
 */
export function readVoiceInputPreference(rawValue: string | null): boolean {
  if (rawValue === null) return false;
  try {
    return JSON.parse(rawValue) === true;
  } catch {
    return false;
  }
}

export type VoiceCaptureState = "idle" | "speech" | "trailing_silence";

export interface VoiceCaptureStepResult {
  nextState: VoiceCaptureState;
  utteranceComplete: boolean;
}

export interface VoiceActivityConfig {
  /** Amplitude (0..1, normalized RMS) above which a sample counts as speech. */
  speechThreshold: number;
  /** How long trailing silence must persist before an utterance is cut. */
  trailingSilenceMs: number;
}

export const DEFAULT_VOICE_ACTIVITY_CONFIG: VoiceActivityConfig = {
  speechThreshold: 0.02,
  trailingSilenceMs: 700,
};

/**
 * One tick of a local amplitude-threshold VAD hysteresis state machine.
 * The caller (impure DOM code polling an AnalyserNode) supplies the
 * current amplitude sample and how long silence has persisted since it
 * last saw a loud sample; this function only decides the transition.
 * "utteranceComplete: true" is the signal to cut and upload the segment
 * captured since the state last left "idle".
 */
export function isSpeechSegment(
  currentState: VoiceCaptureState,
  amplitude: number,
  silenceElapsedMs: number,
  config: VoiceActivityConfig = DEFAULT_VOICE_ACTIVITY_CONFIG,
): VoiceCaptureStepResult {
  const isLoud = amplitude >= config.speechThreshold;

  if (currentState === "idle") {
    return isLoud
      ? { nextState: "speech", utteranceComplete: false }
      : { nextState: "idle", utteranceComplete: false };
  }

  if (currentState === "speech") {
    return isLoud
      ? { nextState: "speech", utteranceComplete: false }
      : { nextState: "trailing_silence", utteranceComplete: false };
  }

  // trailing_silence
  if (isLoud) return { nextState: "speech", utteranceComplete: false };
  if (silenceElapsedMs >= config.trailingSilenceMs) {
    return { nextState: "idle", utteranceComplete: true };
  }
  return { nextState: "trailing_silence", utteranceComplete: false };
}

const MIN_VOICE_TRANSCRIPT_CONFIDENCE = 0.6;

// Every state besides "dormant"/"greeting" already treats any text as a
// real command/reply (JarvisConversationSession.handle() only requires
// the wake phrase while dormant), so a misheard ambient-noise transcript
// is dangerous in all of these, not just AWAITING_CONFIRMATION.
const CONFIDENCE_GATED_STATES = new Set([
  "listening",
  "awaiting_confirmation",
  "processing",
  "responding",
]);

/**
 * Whether a fresh voice transcript should be submitted as a real turn.
 * While dormant/greeting, the backend's exact wake-phrase match is
 * already the safety gate (a random noise transcript won't equal "Hey
 * Jarvis"), so only blank-rejection applies there. In every other active
 * state, any text is treated as a real command, so this also requires a
 * confidence above the stated minimum.
 */
export function shouldSubmitVoiceTranscript(
  transcript: string | null | undefined,
  confidence: number | null | undefined,
  conversationState: string,
): boolean {
  if (!transcript || !transcript.trim()) return false;
  if (!CONFIDENCE_GATED_STATES.has(conversationState)) return true;
  return typeof confidence === "number" && confidence >= MIN_VOICE_TRANSCRIPT_CONFIDENCE;
}
