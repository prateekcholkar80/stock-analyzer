import assert from "node:assert/strict";
import test from "node:test";

import {
  VOICE_PREFERENCE_STORAGE_KEY,
  readVoicePreference,
  shouldPlaySpokenMessage,
} from "../lib/voice.ts";

test("storage key is stable and namespaced", () => {
  assert.equal(VOICE_PREFERENCE_STORAGE_KEY, "jarvis-voice-enabled");
});

test("readVoicePreference defaults to false when nothing is stored", () => {
  assert.equal(readVoicePreference(null), false);
});

test("readVoicePreference defaults to false on corrupt stored values", () => {
  assert.equal(readVoicePreference("not-json"), false);
  assert.equal(readVoicePreference("{broken"), false);
});

test("readVoicePreference parses a real stored boolean", () => {
  assert.equal(readVoicePreference("true"), true);
  assert.equal(readVoicePreference("false"), false);
});

test("readVoicePreference treats a non-boolean stored value as false", () => {
  assert.equal(readVoicePreference('"yes"'), false);
  assert.equal(readVoicePreference("1"), false);
});

test("shouldPlaySpokenMessage requires voice enabled and a real message", () => {
  assert.equal(shouldPlaySpokenMessage(true, "Analysis complete."), true);
  assert.equal(shouldPlaySpokenMessage(false, "Analysis complete."), false);
  assert.equal(shouldPlaySpokenMessage(true, null), false);
  assert.equal(shouldPlaySpokenMessage(true, undefined), false);
  assert.equal(shouldPlaySpokenMessage(true, "   "), false);
});
