import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_VOICE_ACTIVITY_CONFIG,
  VOICE_INPUT_PREFERENCE_STORAGE_KEY,
  isSpeechSegment,
  readVoiceInputPreference,
  shouldSubmitVoiceTranscript,
} from "../lib/voice-capture.ts";

test("storage key is stable and namespaced", () => {
  assert.equal(VOICE_INPUT_PREFERENCE_STORAGE_KEY, "jarvis-voice-input-enabled");
});

test("readVoiceInputPreference defaults to false when nothing is stored", () => {
  assert.equal(readVoiceInputPreference(null), false);
});

test("readVoiceInputPreference defaults to false on corrupt stored values", () => {
  assert.equal(readVoiceInputPreference("not-json"), false);
  assert.equal(readVoiceInputPreference("{broken"), false);
});

test("readVoiceInputPreference parses a real stored boolean", () => {
  assert.equal(readVoiceInputPreference("true"), true);
  assert.equal(readVoiceInputPreference("false"), false);
});

test("readVoiceInputPreference treats a non-boolean stored value as false", () => {
  assert.equal(readVoiceInputPreference('"yes"'), false);
  assert.equal(readVoiceInputPreference("1"), false);
});

test("isSpeechSegment stays idle on quiet samples", () => {
  const result = isSpeechSegment("idle", 0.001, 0);
  assert.equal(result.nextState, "idle");
  assert.equal(result.utteranceComplete, false);
});

test("isSpeechSegment transitions idle -> speech on a loud sample", () => {
  const result = isSpeechSegment("idle", 0.5, 0);
  assert.equal(result.nextState, "speech");
  assert.equal(result.utteranceComplete, false);
});

test("isSpeechSegment stays in speech while loud", () => {
  const result = isSpeechSegment("speech", 0.5, 0);
  assert.equal(result.nextState, "speech");
  assert.equal(result.utteranceComplete, false);
});

test("isSpeechSegment transitions speech -> trailing_silence when it goes quiet", () => {
  const result = isSpeechSegment("speech", 0.001, 0);
  assert.equal(result.nextState, "trailing_silence");
  assert.equal(result.utteranceComplete, false);
});

test("isSpeechSegment returns to speech if loud again during trailing silence", () => {
  const result = isSpeechSegment("trailing_silence", 0.5, 300);
  assert.equal(result.nextState, "speech");
  assert.equal(result.utteranceComplete, false);
});

test("isSpeechSegment stays trailing_silence before the threshold elapses", () => {
  const result = isSpeechSegment("trailing_silence", 0.001, 300);
  assert.equal(result.nextState, "trailing_silence");
  assert.equal(result.utteranceComplete, false);
});

test("isSpeechSegment completes the utterance once trailing silence exceeds the threshold", () => {
  const result = isSpeechSegment(
    "trailing_silence",
    0.001,
    DEFAULT_VOICE_ACTIVITY_CONFIG.trailingSilenceMs,
  );
  assert.equal(result.nextState, "idle");
  assert.equal(result.utteranceComplete, true);
});

test("isSpeechSegment honors a custom config", () => {
  const config = { speechThreshold: 0.1, trailingSilenceMs: 100 };
  assert.equal(isSpeechSegment("idle", 0.05, 0, config).nextState, "idle");
  assert.equal(isSpeechSegment("idle", 0.2, 0, config).nextState, "speech");
  assert.equal(
    isSpeechSegment("trailing_silence", 0.05, 100, config).utteranceComplete,
    true,
  );
});

test("shouldSubmitVoiceTranscript rejects blank or whitespace transcripts", () => {
  assert.equal(shouldSubmitVoiceTranscript(null, 0.9, "listening"), false);
  assert.equal(shouldSubmitVoiceTranscript(undefined, 0.9, "listening"), false);
  assert.equal(shouldSubmitVoiceTranscript("   ", 0.9, "listening"), false);
});

test("shouldSubmitVoiceTranscript requires a confidence above the minimum in every active state", () => {
  for (const state of ["listening", "awaiting_confirmation", "processing", "responding"]) {
    assert.equal(shouldSubmitVoiceTranscript("Analyze Reliance", 0.59, state), false);
    assert.equal(shouldSubmitVoiceTranscript("Analyze Reliance", 0.6, state), true);
    assert.equal(shouldSubmitVoiceTranscript("Analyze Reliance", null, state), false);
    assert.equal(shouldSubmitVoiceTranscript("Analyze Reliance", undefined, state), false);
  }
});

test("shouldSubmitVoiceTranscript does not require confidence while dormant or greeting", () => {
  assert.equal(shouldSubmitVoiceTranscript("Hey Jarvis", null, "dormant"), true);
  assert.equal(shouldSubmitVoiceTranscript("Hey Jarvis", 0.1, "dormant"), true);
  assert.equal(shouldSubmitVoiceTranscript("Hey Jarvis", null, "greeting"), true);
});
