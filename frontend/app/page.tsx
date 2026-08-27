"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { TechnicalChart } from "@/components/TechnicalChart";
import { ExecutiveBriefing } from "@/components/ExecutiveBriefing";
import type {
  Dashboard,
  DashboardQuote,
  DashboardSwingSetup,
  DashboardTimeframeInterpretation,
  TimeframePanel,
} from "@/lib/dashboard";
import {
  SETUP_STATE_META,
  confirmedStepCount,
  timeframeSetupSummary,
} from "@/lib/setup-view";
import { judgeDecisionView } from "@/lib/executive-briefing";
import {
  VOICE_PREFERENCE_STORAGE_KEY,
  readVoicePreference,
  shouldPlaySpokenMessage,
} from "@/lib/voice";
import {
  DEFAULT_VOICE_ACTIVITY_CONFIG,
  VOICE_INPUT_PREFERENCE_STORAGE_KEY,
  isSpeechSegment,
  readVoiceInputPreference,
  shouldSubmitVoiceTranscript,
  type VoiceCaptureState,
} from "@/lib/voice-capture";

import {
  MATRIX_STEPS,
  matchesBear,
  matchesBull,
  matchesDaily,
  matchesEvidence,
  matchesJudge,
  matchesMarket,
  matchesWeekly,
  timeframeLabel,
  workflowProgress,
  type ProgressStatus,
  type WorkflowActivity,
  type WorkflowMatcher,
} from "@/lib/workflow-progress";

type ConversationState = "dormant" | "greeting" | "listening" | "processing" | "responding" | "failed" | "awaiting_confirmation";
type Session = { session_id: string };
type ConversationSnapshot = {
  session_id: string;
  state: ConversationState;
  active_operation_id: string | null;
  display_message: string | null;
  spoken_message: string | null;
};
type ConversationTurn = {
  outcome: "ignored" | "activated" | "completed" | "failed" | "clarification_required" | "busy" | "dispatched" | "confirmation_requested";
  conversation: ConversationSnapshot;
  operation?: { request: { operation_id: string } };
};
type ConversationEvent = {
  event_id: string;
  sequence: number;
  from_state: ConversationState;
  to_state: ConversationState;
  message: string;
};
type OperationFailure = {
  code: string;
  message: string;
  retryable: boolean;
};
type OperationTerminal = {
  status: "completed" | "failed" | "cancelled";
  failure: OperationFailure | null;
};
type OperationStatus = "standby" | "queued" | "running" | "completed" | "failed" | "cancelled";
type AgentTone = "cyan" | "amber" | "rose" | "violet";
type CreatedSession = { session: Session; access_token: string };

const API_BASE = process.env.NEXT_PUBLIC_JARVIS_API_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:8000";

function requestId() {
  return `turn-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function readSse(response: Response, onEvent: (event: string, data: unknown) => void) {
  if (!response.ok || !response.body) throw new Error(`Live stream unavailable (${response.status}).`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length) {
        const raw = data.join("\n");
        try { onEvent(event, JSON.parse(raw)); } catch { onEvent(event, raw); }
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}

function AgentCard({ id, label, specialty, tone, status }: {
  id: string; label: string; specialty: string; tone: AgentTone; status: ProgressStatus;
}) {
  const stateLabel = {
    standby: "Standby",
    active: "Processing",
    complete: "Complete",
    failed: "Failed",
  }[status];
  return (
    <article className={`agent-card ${tone} ${status}`}>
      <div className="agent-glyph" aria-hidden="true"><span>{id}</span></div>
      <div><p className="eyebrow">{specialty}</p><h3>{label}</h3></div>
      <span className={`agent-state ${status}`}>{stateLabel}</span>
    </article>
  );
}

function messageForTurn(turn: ConversationTurn): string {
  if (turn.conversation.display_message) return turn.conversation.display_message;
  if (turn.outcome === "ignored") {
    return "Jarvis is in standby. Say ‘Hey Jarvis’ before issuing a research command.";
  }
  if (turn.outcome === "busy") return "Jarvis is already coordinating an active operation.";
  if (turn.outcome === "dispatched") return "Jarvis dispatched the research workflow.";
  if (turn.outcome === "activated") return "Jarvis is awake and listening.";
  return "Jarvis received the conversation turn.";
}

function zoneLabel(zone: TimeframePanel["nearest_support"]): string {
  if (!zone) return "No qualified zone";
  return `${zone.lower_price.toFixed(2)}–${zone.upper_price.toFixed(2)}`;
}

function SetupSequence({ setup }: { setup: DashboardSwingSetup }) {
  const confirmed = confirmedStepCount(setup);
  return (
    <section className={`setup-sequence ${setup.side}`} aria-label={`${setup.side} setup progression`}>
      <header>
        <div>
          <span>{setup.side} setup</span>
          <b>{confirmed}/{setup.steps.length}</b>
        </div>
        <small>confirmed</small>
      </header>
      <ol>
        {setup.steps.map((step) => {
          const state = SETUP_STATE_META[step.state];
          return (
            <li className={step.state} key={step.step_id} title={step.explanation}>
              <i aria-hidden="true">{state.symbol}</i>
              <span>{step.label}</span>
              <em>{state.label}</em>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function TimeframeInterpretation({
  interpretation,
}: {
  interpretation: DashboardTimeframeInterpretation;
}) {
  return (
    <section className="timeframe-interpretation" aria-label={`${interpretation.timeframe} setup interpretation`}>
      <div className="interpretation-heading">
        <div>
          <p className="eyebrow">Deterministic setup matrix</p>
          <strong className={`condition ${interpretation.market_condition}`}>
            {interpretation.market_condition.replaceAll("_", " ")}
          </strong>
        </div>
        <span>{timeframeSetupSummary(interpretation)}</span>
      </div>
      <div className="setup-comparison">
        <SetupSequence setup={interpretation.bullish_setup} />
        <SetupSequence setup={interpretation.bearish_setup} />
      </div>
      <details className="interpretation-rationale">
        <summary>Why this timeframe reads {interpretation.market_condition}</summary>
        <p>{interpretation.rationale}</p>
      </details>
    </section>
  );
}

function SwingProtocol({
  interpretation,
}: {
  interpretation: NonNullable<Dashboard["interpretation"]>;
}) {
  const targetLabel = (target: typeof interpretation.risk_reward.target_2r) =>
    target.target_price === null
      ? target.feasibility.replaceAll("_", " ")
      : `₹${target.target_price.toFixed(2)} · ${target.feasibility.replaceAll("_", " ")}`;
  return (
    <article className="swing-protocol glass-panel">
      <div className="protocol-decision">
        <p className="eyebrow">Deterministic swing protocol</p>
        <strong className={interpretation.trade_decision.decision}>
          {interpretation.trade_decision.decision.replaceAll("_", " ")}
        </strong>
        <span>{interpretation.trade_decision.market_condition} market condition</span>
      </div>
      <dl className="protocol-metrics">
        <div><dt>Timeframes</dt><dd>{interpretation.alignment.replaceAll("_", " ")}</dd></div>
        <div><dt>Tactical trigger</dt><dd>{interpretation.tactical_readiness.replaceAll("_", " ")}</dd></div>
        <div><dt>Structural risk</dt><dd>{interpretation.structural_risk}</dd></div>
        <div><dt>2R target</dt><dd>{targetLabel(interpretation.risk_reward.target_2r)}</dd></div>
        <div><dt>3R target</dt><dd>{targetLabel(interpretation.risk_reward.target_3r)}</dd></div>
      </dl>
      <div className="protocol-rationale">
        <p>{interpretation.rationale}</p>
        <ul>
          {interpretation.decision_change_conditions.map((condition) => (
            <li key={condition}>{condition}</li>
          ))}
        </ul>
      </div>
    </article>
  );
}

function TimeframeResult({ panel, quote, interpretation }: {
  panel: TimeframePanel;
  quote: DashboardQuote | null;
  interpretation?: DashboardTimeframeInterpretation;
}) {
  return (
    <article className="result-panel glass-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">{panel.timeframe} analyst · {panel.interval}</p>
          <h2>{panel.stance.replaceAll("_", " ")}</h2>
        </div>
        <strong className={panel.score >= 0 ? "positive" : "negative"}>{panel.score > 0 ? "+" : ""}{panel.score.toFixed(1)}</strong>
      </div>
      <div className="price-context-grid">
        <div className="price-context live">
          <span>Broker LTP</span>
          <b>{quote ? `₹${quote.price.toFixed(2)}` : "Unavailable"}</b>
          <small>{quote ? `Observed ${new Date(quote.observed_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })} IST` : "No quote attached to this operation"}</small>
        </div>
        <div className="price-context">
          <span>{panel.timeframe} analysis close</span>
          <b>₹{panel.current_close.toFixed(2)}</b>
          <small>Completed candle ending {new Date(panel.evaluated_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })} IST</small>
        </div>
        <div className="price-context support">
          <span>Immediate support</span>
          <b>{zoneLabel(panel.nearest_support)}</b>
          <small>{panel.nearest_support ? `${panel.nearest_support.lifecycle_status.replaceAll("_", " ")} · ${panel.nearest_support.distance_percentage.toFixed(2)}% away` : "No confirmed multi-touch support below price"}</small>
        </div>
        <div className="price-context resistance">
          <span>Immediate resistance</span>
          <b>{zoneLabel(panel.nearest_resistance)}</b>
          <small>{panel.nearest_resistance ? `${panel.nearest_resistance.lifecycle_status.replaceAll("_", " ")} · ${panel.nearest_resistance.distance_percentage.toFixed(2)}% away` : "No confirmed multi-touch resistance above price"}</small>
        </div>
      </div>
      {interpretation && <TimeframeInterpretation interpretation={interpretation} />}
      <TechnicalChart panel={panel} quote={quote} />
      <details className="raw-evidence-drawer">
        <summary>Technical evidence ledger <b>{panel.evidence.length} signals</b></summary>
        <div className="evidence-list">
          {panel.evidence.map((item) => (
            <div className={item.decisive ? "evidence decisive" : "evidence"} key={item.evidence_id}>
              <span>{item.name}</span><b>{item.direction}</b>
            </div>
          ))}
        </div>
      </details>
    </article>
  );
}

export default function Home() {
  const [session, setSession] = useState<Session | null>(null);
  const [token, setToken] = useState("");
  const [conversation, setConversation] = useState<ConversationSnapshot | null>(null);
  const [command, setCommand] = useState("");
  const [activities, setActivities] = useState<WorkflowActivity[]>([]);
  const [activeOperation, setActiveOperation] = useState<string | null>(null);
  const [displayOperation, setDisplayOperation] = useState<string | null>(null);
  const [operationStatus, setOperationStatus] = useState<OperationStatus>("standby");
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [notice, setNotice] = useState("Establishing secure research link…");
  const [error, setError] = useState<string | null>(null);
  const [istClock, setIstClock] = useState("--:--");
  const [voiceEnabled, setVoiceEnabled] = useState(false);
  const [voicePreferencesLoaded, setVoicePreferencesLoaded] = useState(false);
  const [voiceInputEnabled, setVoiceInputEnabled] = useState(false);
  const [voiceInputPreferencesLoaded, setVoiceInputPreferencesLoaded] = useState(false);
  const conversationAbort = useRef<AbortController | null>(null);
  const workflowAbort = useRef<AbortController | null>(null);
  const micStreamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const recorderChunksRef = useRef<Blob[]>([]);
  const captureIntervalRef = useRef<number | null>(null);
  const captureSuspendedRef = useRef(false);
  const captureStateRef = useRef<VoiceCaptureState>("idle");
  const silenceElapsedRef = useRef(0);
  const conversationRef = useRef<ConversationSnapshot | null>(null);
  const authHeaders = useMemo(() => ({ "X-Jarvis-Session-Token": token }), [token]);

  useEffect(() => {
    const formatter = new Intl.DateTimeFormat("en-IN", {
      hour: "numeric",
      minute: "2-digit",
      timeZone: "Asia/Kolkata",
    });
    const updateClock = () => setIstClock(formatter.format(new Date()));
    updateClock();
    const timer = window.setInterval(updateClock, 30_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      try {
        setVoiceEnabled(
          readVoicePreference(window.localStorage.getItem(VOICE_PREFERENCE_STORAGE_KEY)),
        );
      } catch {
        // Corrupt device-local preferences must not block conversation rendering.
      } finally {
        setVoicePreferencesLoaded(true);
      }
    });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!voicePreferencesLoaded) return;
    try {
      window.localStorage.setItem(VOICE_PREFERENCE_STORAGE_KEY, JSON.stringify(voiceEnabled));
    } catch {
      // Storage can be unavailable in privacy mode; the toggle still works.
    }
  }, [voiceEnabled, voicePreferencesLoaded]);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      try {
        setVoiceInputEnabled(
          readVoiceInputPreference(window.localStorage.getItem(VOICE_INPUT_PREFERENCE_STORAGE_KEY)),
        );
      } catch {
        // Corrupt device-local preferences must not block conversation rendering.
      } finally {
        setVoiceInputPreferencesLoaded(true);
      }
    });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!voiceInputPreferencesLoaded) return;
    try {
      window.localStorage.setItem(VOICE_INPUT_PREFERENCE_STORAGE_KEY, JSON.stringify(voiceInputEnabled));
    } catch {
      // Storage can be unavailable in privacy mode; the toggle still works.
    }
  }, [voiceInputEnabled, voiceInputPreferencesLoaded]);

  useEffect(() => {
    conversationRef.current = conversation;
  }, [conversation]);

  useEffect(() => {
    let disposed = false;
    let created: CreatedSession | null = null;

    const closeCreatedSession = (value: CreatedSession | null) => {
      if (!value) return;
      void fetch(`${API_BASE}/api/v1/sessions/${value.session.session_id}`, {
        method: "DELETE",
        headers: { "X-Jarvis-Session-Token": value.access_token },
        keepalive: true,
      }).catch(() => undefined);
      if (created?.session.session_id === value.session.session_id) {
        created = null;
      }
    };

    const handlePageHide = () => closeCreatedSession(created);
    const handlePageShow = (event: PageTransitionEvent) => {
      // A back-forward-cache restore revives JavaScript state without mounting
      // the component again. Reload so it receives a fresh backend session.
      if (event.persisted) window.location.reload();
    };

    window.addEventListener("pagehide", handlePageHide);
    window.addEventListener("pageshow", handlePageShow);
    fetch(`${API_BASE}/api/v1/sessions`, { method: "POST" })
      .then(async (response) => {
        if (!response.ok) throw new Error("Jarvis research core is offline.");
        return response.json() as Promise<CreatedSession>;
      })
      .then((body) => {
        if (disposed) {
          closeCreatedSession(body);
          return;
        }
        created = body;
        setSession(body.session);
        setToken(body.access_token);
        setConversation({
          session_id: body.session.session_id,
          state: "dormant",
          active_operation_id: null,
          display_message: "Jarvis is in standby.",
          spoken_message: null,
        });
        setNotice("Neural link secure. Wake phrase required.");
      })
      .catch((reason) => {
        if (!disposed) {
          setError(reason instanceof Error ? reason.message : "Jarvis is offline.");
          setNotice("Research core unavailable");
        }
      });
    return () => {
      disposed = true;
      window.removeEventListener("pagehide", handlePageHide);
      window.removeEventListener("pageshow", handlePageShow);
      conversationAbort.current?.abort();
      workflowAbort.current?.abort();
      closeCreatedSession(created);
    };
  }, []);

  const fetchDashboard = useCallback(async (operationId: string) => {
    if (!session || !token) return;
    const response = await fetch(
      `${API_BASE}/api/v1/sessions/${session.session_id}/operations/${operationId}/dashboard`,
      { headers: authHeaders },
    );
    if (!response.ok) throw new Error("Jarvis completed, but the dashboard projection is unavailable.");
    setDashboard(await response.json());
    setActiveOperation(null);
  }, [authHeaders, session, token]);

  const streamWorkflow = useCallback((operationId: string) => {
    if (!session || !token) return;
    workflowAbort.current?.abort();
    const controller = new AbortController();
    workflowAbort.current = controller;
    fetch(`${API_BASE}/api/v1/sessions/${session.session_id}/operations/${operationId}/events/stream`, {
      headers: authHeaders,
      signal: controller.signal,
    })
      .then((response) => {
        if (response.ok) setOperationStatus("running");
        return readSse(response, (event, data) => {
        if (event === "workflow") {
          const activity = data as WorkflowActivity;
          setActivities((current) => current.some((item) => item.event_id === activity.event_id)
            ? current
            : [...current, activity].sort((a, b) => a.sequence - b.sequence));
          setNotice(activity.message);
        }
        if (event === "terminal") {
          const terminal = data as OperationTerminal;
          setOperationStatus(terminal.status);
          if (terminal.status === "completed") {
            fetchDashboard(operationId).catch((reason) => setError(reason.message));
          } else {
            setActiveOperation(null);
            setError(
              terminal.failure?.message
                ?? (terminal.status === "cancelled"
                  ? "Research operation was cancelled."
                  : "Research operation failed without a safe explanation."),
            );
          }
        }
        });
      })
      .catch((reason) => {
        if (reason.name !== "AbortError") {
          setError(reason.message);
          setOperationStatus("failed");
        }
      });
  }, [authHeaders, fetchDashboard, session, token]);

  const playSpokenMessage = useCallback(async (message: string | null | undefined) => {
    if (!shouldPlaySpokenMessage(voiceEnabled, message) || !session || !token) return;
    try {
      const response = await fetch(`${API_BASE}/api/v1/sessions/${session.session_id}/speech`, {
        method: "POST",
        headers: { ...authHeaders, "Content-Type": "application/json" },
        body: JSON.stringify({ text: message }),
      });
      // TTS not configured, or synthesis failed -- never block conversation
      // text, which has already rendered by the time this runs.
      if (!response.ok) return;
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      const resumeCapture = () => {
        URL.revokeObjectURL(url);
        captureSuspendedRef.current = false;
        recorderChunksRef.current = [];
        captureStateRef.current = "idle";
        silenceElapsedRef.current = 0;
      };
      audio.onended = resumeCapture;
      audio.onerror = resumeCapture;
      // Mute VAD/mic capture for the duration of playback so Jarvis's own
      // spoken reply can never be picked up by the mic and mistaken for a
      // user utterance.
      captureSuspendedRef.current = true;
      recorderChunksRef.current = [];
      captureStateRef.current = "idle";
      silenceElapsedRef.current = 0;
      void audio.play();
    } catch {
      // Voice playback failure must never block conversation rendering.
    }
  }, [authHeaders, session, token, voiceEnabled]);

  useEffect(() => {
    if (!session || !token) return;
    conversationAbort.current?.abort();
    const controller = new AbortController();
    conversationAbort.current = controller;
    fetch(`${API_BASE}/api/v1/sessions/${session.session_id}/conversation/events/stream`, {
      headers: authHeaders,
      signal: controller.signal,
    })
      .then((response) => readSse(response, (event, data) => {
        if (event === "conversation") {
          const item = data as ConversationEvent;
          setConversation((current) => current ? {
            ...current,
            state: item.to_state,
          } : current);
          if (!["greeting", "listening"].includes(item.to_state)) {
            setNotice(item.message);
          }
        }
        if (event === "conversation-terminal") {
          const snapshot = data as ConversationSnapshot;
          setConversation(snapshot);
          void playSpokenMessage(snapshot.spoken_message);
        }
      }))
      .catch((reason) => {
        if (reason.name !== "AbortError") setError(reason.message);
      });
    return () => controller.abort();
  }, [authHeaders, playSpokenMessage, session, token]);

  const sendUtterance = useCallback(async (text: string, channel: "text" | "voice" = "text") => {
    const normalized = text.trim();
    if (!session || !token || !normalized) return;
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/api/v1/sessions/${session.session_id}/conversation/turns`, {
        method: "POST",
        headers: { ...authHeaders, "Content-Type": "application/json" },
        body: JSON.stringify({ idempotency_key: requestId(), input_channel: channel, text: normalized }),
      });
      if (!response.ok) throw new Error(`Jarvis rejected the request (${response.status}).`);
      const turn = await response.json() as ConversationTurn;
      setConversation(turn.conversation);
      void playSpokenMessage(turn.conversation.spoken_message);
      setNotice(messageForTurn(turn));
      if (channel === "text") setCommand("");
      const operationId = turn.operation?.request.operation_id;
      if (operationId) {
        setDashboard(null);
        setActivities([]);
        setActiveOperation(operationId);
        setDisplayOperation(operationId);
        setOperationStatus("queued");
        streamWorkflow(operationId);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Request failed.");
    }
  }, [authHeaders, playSpokenMessage, session, streamWorkflow, token]);

  const sendText = useCallback((text: string) => sendUtterance(text, "text"), [sendUtterance]);

  const sendUtteranceRef = useRef(sendUtterance);
  useEffect(() => {
    sendUtteranceRef.current = sendUtterance;
  }, [sendUtterance]);

  const uploadVoiceSegment = useCallback(async () => {
    const chunks = recorderChunksRef.current;
    recorderChunksRef.current = [];
    if (!session || !token || chunks.length === 0) return;
    const mimeType = recorderRef.current?.mimeType || "audio/webm";
    const blob = new Blob(chunks, { type: mimeType });
    if (blob.size === 0) return;
    try {
      const response = await fetch(`${API_BASE}/api/v1/sessions/${session.session_id}/transcribe`, {
        method: "POST",
        headers: { ...authHeaders, "Content-Type": mimeType },
        body: blob,
      });
      // A failed or unconfigured transcription must never surface as a
      // conversation error -- it silently discards this utterance and the
      // VAD loop keeps listening for the next one.
      if (!response.ok) return;
      const result = await response.json() as { transcript: string | null; confidence: number | null };
      const state = conversationRef.current?.state ?? "dormant";
      if (shouldSubmitVoiceTranscript(result.transcript, result.confidence, state)) {
        void sendUtteranceRef.current(result.transcript as string, "voice");
      }
    } catch {
      // Voice transcription failure must never block conversation rendering.
    }
  }, [authHeaders, session, token]);

  const stopVoiceCapture = useCallback(() => {
    if (captureIntervalRef.current !== null) {
      window.clearInterval(captureIntervalRef.current);
      captureIntervalRef.current = null;
    }
    recorderRef.current?.stop();
    recorderRef.current = null;
    recorderChunksRef.current = [];
    analyserRef.current = null;
    audioContextRef.current?.close().catch(() => undefined);
    audioContextRef.current = null;
    micStreamRef.current?.getTracks().forEach((track) => track.stop());
    micStreamRef.current = null;
    captureStateRef.current = "idle";
    silenceElapsedRef.current = 0;
    captureSuspendedRef.current = false;
  }, []);

  const startVoiceCapture = useCallback(async () => {
    if (micStreamRef.current) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      micStreamRef.current = stream;
      const audioContext = new AudioContext();
      audioContextRef.current = audioContext;
      const source = audioContext.createMediaStreamSource(stream);
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 2048;
      source.connect(analyser);
      analyserRef.current = analyser;

      const recorder = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
      recorderRef.current = recorder;
      recorderChunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) recorderChunksRef.current.push(event.data);
      };
      recorder.start(250);

      captureStateRef.current = "idle";
      silenceElapsedRef.current = 0;
      const sampleBuffer = new Uint8Array(analyser.fftSize);
      const tickIntervalMs = 100;
      captureIntervalRef.current = window.setInterval(() => {
        if (captureSuspendedRef.current) return;
        analyser.getByteTimeDomainData(sampleBuffer);
        let sumSquares = 0;
        for (let i = 0; i < sampleBuffer.length; i += 1) {
          const centered = (sampleBuffer[i] - 128) / 128;
          sumSquares += centered * centered;
        }
        const amplitude = Math.sqrt(sumSquares / sampleBuffer.length);
        const wasLoud = amplitude >= DEFAULT_VOICE_ACTIVITY_CONFIG.speechThreshold;
        silenceElapsedRef.current = wasLoud ? 0 : silenceElapsedRef.current + tickIntervalMs;
        const result = isSpeechSegment(captureStateRef.current, amplitude, silenceElapsedRef.current);
        captureStateRef.current = result.nextState;
        if (result.utteranceComplete) void uploadVoiceSegment();
      }, tickIntervalMs);
    } catch {
      setVoiceInputEnabled(false);
      setError("Microphone access was denied or unavailable.");
    }
  }, [uploadVoiceSegment]);

  useEffect(() => {
    if (!voiceInputEnabled || !session || !token) {
      stopVoiceCapture();
      return;
    }
    void startVoiceCapture();
    return () => stopVoiceCapture();
  }, [voiceInputEnabled, session, token, startVoiceCapture, stopVoiceCapture]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void sendText(command);
  };

  const progress = (matches: WorkflowMatcher) => workflowProgress(activities, matches);
  const agentStates = {
    research: progress(matchesMarket),
    daily: progress(matchesDaily),
    weekly: progress(matchesWeekly),
    bull: progress(matchesBull),
    bear: progress(matchesBear),
    judge: progress((item) => matchesEvidence(item) || matchesJudge(item)),
  };
  const recentActivities = activities.slice(-8).reverse();
  const state = conversation?.state ?? "dormant";
  const decisionView = dashboard
    ? judgeDecisionView(
      dashboard.debate.winner,
      dashboard.debate.confidence_percentage,
      dashboard.trade_plan.disposition,
    )
    : null;

  return (
    <main className="jarvis-shell">
      <div className="ambient-grid" aria-hidden="true" />
      <header className="topbar">
        <div className="brand-lockup">
          <span className="brand-mark">J</span>
          <div><p className="eyebrow">Investment intelligence</p><h1>JARVIS</h1></div>
        </div>
        <div className="system-meta">
          <span><i className={error ? "dot danger" : "dot"} />{error ? "Attention required" : "Systems nominal"}</span>
          <span>IST · {istClock}</span>
        </div>
      </header>

      <section className="command-deck" aria-label="Jarvis research command center">
        <aside className="agent-column left-agents">
          <p className="section-label">Research division</p>
          <AgentCard id="R" label="Research Analyst" specialty="Market intelligence" tone="cyan" status={agentStates.research.status} />
          <AgentCard id="D" label="Daily Analyst" specialty="Tactical structure" tone="violet" status={agentStates.daily.status} />
          <AgentCard id="W" label="Weekly Analyst" specialty="Strategic structure" tone="violet" status={agentStates.weekly.status} />
        </aside>

        <section className="reactor-zone">
          <div className={`reactor ${state} ${activeOperation ? "engaged" : ""}`}>
            <div className="orbit orbit-one"><i /><i /><i /></div>
            <div className="orbit orbit-two"><i /><i /><i /><i /></div>
            <button
              className="reactor-core"
              onClick={state === "dormant" ? () => void sendText("Hey Jarvis") : undefined}
              aria-label={state === "dormant" ? "Wake Jarvis" : `Jarvis is ${state}`}
            >
              <span className="core-letter">J</span><span className="core-state">{state}</span>
            </button>
          </div>
          <div className="jarvis-message" aria-live="polite">
            <p className="eyebrow">Jarvis communication</p><h2>{notice}</h2>
            {error && <p className="error-message">{error}</p>}
          </div>
          <form id="command-form" className="command-bar" onSubmit={submit}>
            <span className="prompt-symbol">›</span>
            <input
              value={command}
              onChange={(event) => setCommand(event.target.value)}
              placeholder={state === "dormant" ? 'Type “Hey Jarvis” to activate' : "Ask Jarvis to analyse a company"}
              aria-label="Message Jarvis"
              disabled={!session || Boolean(activeOperation && !dashboard)}
            />
            <button type="submit" disabled={!command.trim() || !session}>Transmit</button>
          </form>
          <div className="voice-toggles">
            <label className="voice-toggle">
              <input
                type="checkbox"
                checked={voiceInputEnabled}
                onChange={(event) => setVoiceInputEnabled(event.target.checked)}
              />
              Voice input
            </label>
            <label className="voice-toggle">
              <input
                type="checkbox"
                checked={voiceEnabled}
                onChange={(event) => setVoiceEnabled(event.target.checked)}
              />
              Voice replies
            </label>
          </div>
        </section>

        <aside className="agent-column right-agents">
          <p className="section-label">Debate chamber</p>
          <AgentCard id="♉" label="Bull" specialty="Constructive advocate" tone="amber" status={agentStates.bull.status} />
          <AgentCard id="♙" label="Bear" specialty="Risk advocate" tone="rose" status={agentStates.bear.status} />
          <AgentCard id="J" label="Senior Judge" specialty="Evidence synthesis" tone="cyan" status={agentStates.judge.status} />
        </aside>
      </section>

      <section className="progress-matrix glass-panel" aria-label="Live workflow matrix">
        <div className="matrix-heading">
          <span>Operation matrix</span>
          <div className="operation-identity">
            <b>{displayOperation ?? "Awaiting command"}</b>
            <em className={`operation-status ${operationStatus}`}>{operationStatus}</em>
          </div>
        </div>
        <div className="matrix-track">
          {MATRIX_STEPS.map((step) => {
            const stepProgress = progress(step.matches);
            return (
              <div className={`matrix-step ${stepProgress.status}`} key={step.key}>
                <i />
                <span>{step.label}</span>
                <small>
                  {stepProgress.roundNumber ? `Round ${stepProgress.roundNumber} · ` : ""}
                  {stepProgress.status}
                </small>
              </div>
            );
          })}
        </div>
        <div className="activity-console" aria-live="polite">
          <div className="activity-console-heading">
            <span>Live agent activity</span>
            <b>{activities.length} verified events</b>
          </div>
          {recentActivities.length ? (
            <ol className="activity-feed">
              {recentActivities.map((item) => (
                <li className={item.state} key={item.event_id}>
                  <span className="activity-sequence">{String(item.sequence).padStart(2, "0")}</span>
                  <div>
                    <strong>{item.activity.participant_label}</strong>
                    <p>{item.message}</p>
                  </div>
                  <span className="activity-meta">
                    {timeframeLabel(item.activity.timeframe)}
                    {item.round_number ? ` · R${item.round_number}` : ""}
                  </span>
                  <em>{item.state}</em>
                </li>
              ))}
            </ol>
          ) : (
            <p className="activity-empty">Agent telemetry will appear when Jarvis dispatches a research operation.</p>
          )}
        </div>
      </section>

      {dashboard && (
        <section className="results-grid" aria-label={`${dashboard.symbol} research result`}>
          <div className="result-title">
            <div>
              <p className="eyebrow">Completed research dossier · {dashboard.exchange}</p>
              <h2>{dashboard.symbol}</h2>
              <p className="result-quote">
                {dashboard.latest_quote
                  ? `Broker LTP ₹${dashboard.latest_quote.price.toFixed(2)} · observed ${new Date(dashboard.latest_quote.observed_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })} IST`
                  : "Broker LTP unavailable for this completed operation"}
              </p>
            </div>
            <div className={`verdict decision-${decisionView?.tone}`}>
              <span>Judge verdict · {decisionView?.actionLabel}</span>
              <b>{decisionView?.verdictLabel}</b>
              <em>{decisionView?.confidenceLabel}</em>
            </div>
          </div>
          {dashboard.interpretation && <SwingProtocol interpretation={dashboard.interpretation} />}
          <TimeframeResult
            panel={dashboard.daily}
            quote={dashboard.latest_quote}
            interpretation={dashboard.interpretation?.daily}
          />
          <TimeframeResult
            panel={dashboard.weekly}
            quote={dashboard.latest_quote}
            interpretation={dashboard.interpretation?.weekly}
          />
          <article className="debate-panel glass-panel">
            <div className="debate-side bull"><p className="eyebrow">Bull case</p><p>{dashboard.debate.bull_case_summary}</p></div>
            <div className={`judge-seal decision-${decisionView?.tone}`}>
              <span>JUDGE</span>
              <b>{dashboard.debate.confidence_percentage.toFixed(0)}%</b>
              <small>{decisionView?.verdictLabel}</small>
            </div>
            <div className="debate-side bear"><p className="eyebrow">Bear case</p><p>{dashboard.debate.bear_case_summary}</p></div>
          </article>
          <ExecutiveBriefing dashboard={dashboard} />
        </section>
      )}

      <footer><span>Research, not guaranteed investment advice.</span><span>Charts powered by <a href="https://plotly.com/" target="_blank" rel="noreferrer">Plotly</a> · Evidence chain · {activities.length} events verified</span></footer>
    </main>
  );
}
