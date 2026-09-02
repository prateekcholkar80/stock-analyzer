"use client";

import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";

import { TechnicalChart } from "@/components/TechnicalChart";
import { ExecutiveBriefing } from "@/components/ExecutiveBriefing";
import { HolographicAgent } from "@/components/HolographicAgent";
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
  ProviderSessionClientError,
  createProviderSessionClient,
  type ProviderSessionLifecycle,
  type ProviderSessionTarget,
} from "@/lib/provider-session";
import { AGENT_VISUALS, type AgentVisualSpec } from "@/lib/agent-visuals";
import { NeuralAudioEngine } from "@/lib/neural-audio";
import {
  fetchBenchmarkingFinancials,
  fetchStructuredFinancialDocument,
  type BenchmarkingFinancialsDocumentResponse,
  type BenchmarkingFinancialsReference,
  type StructuredDocumentReference,
  type StructuredFinancialDocumentResponse,
} from "@/lib/financial-documents";

import {
  MATRIX_STEPS,
  matchesBear,
  matchesBull,
  matchesDaily,
  matchesEvidence,
  matchesBriefing,
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
type OperationResultResponse = {
  schema_version: "jarvis.http_operation_result.v1";
  output: {
    structured_document_references?: StructuredDocumentReference[];
    benchmarking_financials_reference?: BenchmarkingFinancialsReference | null;
  } | null;
};

const API_BASE = process.env.NEXT_PUBLIC_JARVIS_API_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:8000";
const SESSION_BOOTSTRAP_RETRY_MS = 2_000;
const TIJORI_CONNECTION_ID = process.env.NEXT_PUBLIC_JARVIS_TIJORI_CONNECTION_ID?.trim() ?? "";
const TIJORI_ACCOUNT_REFERENCE_HASH = process.env.NEXT_PUBLIC_JARVIS_TIJORI_ACCOUNT_REFERENCE_HASH?.trim() || null;
const TIJORI_TARGET: ProviderSessionTarget | null = TIJORI_CONNECTION_ID
  ? {
    provider_connection_id: TIJORI_CONNECTION_ID,
    provider: "tijori",
    account_reference_hash: TIJORI_ACCOUNT_REFERENCE_HASH,
  }
  : null;
const RECOVERABLE_INSTRUMENT_FAILURES = new Set([
  "instrument.not_found",
  "instrument.ambiguous",
]);
const FINANCIAL_DOCUMENT_LABELS: Record<StructuredDocumentReference["document_type"], string> = {
  growth_table: "Growth table",
  balance_sheet: "Balance sheet",
  profit_and_loss: "Profit & loss",
  cash_flow: "Cash flow",
  ratios: "Ratios",
  quarterly_results: "Quarterly results",
};

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

function AgentCard({ id, label, specialty, tone, status, visual, statusLabel }: {
  id: string;
  label: string;
  specialty: string;
  tone: AgentTone;
  status: ProgressStatus;
  visual?: AgentVisualSpec;
  statusLabel?: string;
}) {
  const stateLabel = statusLabel ?? ({
    standby: "Standby",
    active: "Processing",
    complete: "Complete",
    failed: "Failed",
  }[status]);
  return (
    <article className={`agent-card ${tone} ${status} ${visual ? "holographic" : ""}`}>
      {visual
        ? <HolographicAgent spec={visual} status={status} />
        : <div className="agent-glyph" aria-hidden="true"><span>{id}</span></div>}
      <div><p className="eyebrow">{specialty}</p><h3>{label}</h3></div>
      <span className={`agent-state ${status}`}>{stateLabel}</span>
    </article>
  );
}

type CommunicationDirection = "to-agent" | "to-jarvis" | "bidirectional";

function CommunicationLink({
  agent,
  status,
  direction,
  label,
  style,
}: {
  agent: "research" | "daily" | "weekly" | "fundamental" | "bull" | "bear" | "judge";
  status: ProgressStatus;
  direction: CommunicationDirection;
  label: string;
  style?: CSSProperties;
}) {
  return (
    <span className={`communication-link link-${agent} ${status} ${direction}`} style={style}>
      <span className="communication-track" />
      <i className="signal-packet packet-primary" />
      <i className="signal-packet packet-return" />
      <em>{label}</em>
    </span>
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
  const [structuredDocuments, setStructuredDocuments] = useState<StructuredDocumentReference[]>([]);
  const [benchmarkingReference, setBenchmarkingReference] = useState<BenchmarkingFinancialsReference | null>(null);
  const [selectedFinancialDocument, setSelectedFinancialDocument] = useState<StructuredFinancialDocumentResponse | null>(null);
  const [selectedBenchmarking, setSelectedBenchmarking] = useState<BenchmarkingFinancialsDocumentResponse | null>(null);
  const [financialDocumentLoading, setFinancialDocumentLoading] = useState<string | null>(null);
  const [financialDocumentError, setFinancialDocumentError] = useState<string | null>(null);
  const [notice, setNotice] = useState("Establishing secure research link…");
  const [error, setError] = useState<string | null>(null);
  const [istClock, setIstClock] = useState("--:--");
  const [voiceEnabled, setVoiceEnabled] = useState(false);
  const [voicePreferencesLoaded, setVoicePreferencesLoaded] = useState(false);
  const [voiceInputEnabled, setVoiceInputEnabled] = useState(false);
  const [voiceInputPreferencesLoaded, setVoiceInputPreferencesLoaded] = useState(false);
  const [soundEffectsEnabled, setSoundEffectsEnabled] = useState(false);
  const [providerLifecycle, setProviderLifecycle] = useState<ProviderSessionLifecycle | null>(null);
  const [providerCommand, setProviderCommand] = useState<"status" | "provision" | "revoke" | null>(null);
  const [providerNotice, setProviderNotice] = useState<string | null>(
    TIJORI_TARGET
      ? null
      : "Tijori connection controls are not configured for this browser build.",
  );
  const conversationAbort = useRef<AbortController | null>(null);
  const workflowAbort = useRef<AbortController | null>(null);
  const neuralAudioRef = useRef<NeuralAudioEngine | null>(null);
  const micStreamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const recorderChunksRef = useRef<Blob[]>([]);
  const captureIntervalRef = useRef<number | null>(null);
  const captureRequestedRef = useRef(false);
  const captureSuspendedRef = useRef(false);
  const captureStateRef = useRef<VoiceCaptureState>("idle");
  const silenceElapsedRef = useRef(0);
  const conversationRef = useRef<ConversationSnapshot | null>(null);
  const commandDeckRef = useRef<HTMLElement | null>(null);
  const reactorCoreRef = useRef<HTMLButtonElement | null>(null);
  const researchNodeRefs = useRef<Record<"research" | "daily" | "weekly" | "fundamental", HTMLDivElement | null>>({
    research: null,
    daily: null,
    weekly: null,
    fundamental: null,
  });
  const [researchLinkGeometry, setResearchLinkGeometry] = useState<Partial<Record<"research" | "daily" | "weekly" | "fundamental", CSSProperties>>>({});
  const debateNodeRefs = useRef<Record<"bull" | "bear" | "judge", HTMLDivElement | null>>({
    bull: null,
    bear: null,
    judge: null,
  });
  const [debateLinkGeometry, setDebateLinkGeometry] = useState<Partial<Record<"bull" | "bear" | "judge", CSSProperties>>>({});
  const authHeaders = useMemo(() => ({ "X-Jarvis-Session-Token": token }), [token]);
  const providerClient = useMemo(() => (
    session && token && TIJORI_TARGET
      ? createProviderSessionClient({
        apiBase: API_BASE,
        sessionId: session.session_id,
        accessToken: token,
      })
      : null
  ), [session, token]);

  useEffect(() => {
    neuralAudioRef.current = new NeuralAudioEngine();
    return () => { void neuralAudioRef.current?.dispose(); };
  }, []);

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
    let animationFrame = 0;
    const updateGeometry = () => {
      window.cancelAnimationFrame(animationFrame);
      animationFrame = window.requestAnimationFrame(() => {
        const core = reactorCoreRef.current?.getBoundingClientRect();
        if (!core) return;
        const targetX = core.left + core.width / 2;
        const targetY = core.top + core.height / 2;
        const next: Partial<Record<"research" | "daily" | "weekly" | "fundamental", CSSProperties>> = {};
        for (const key of ["research", "daily", "weekly", "fundamental"] as const) {
          const node = researchNodeRefs.current[key]?.getBoundingClientRect();
          if (!node) continue;
          const deltaX = targetX - node.right;
          const deltaY = targetY - node.top;
          next[key] = {
            width: `${Math.hypot(deltaX, deltaY).toFixed(1)}px`,
            transform: `rotate(${Math.atan2(deltaY, deltaX).toFixed(5)}rad)`,
          };
        }
        setResearchLinkGeometry(next);
        const debateNext: Partial<Record<"bull" | "bear" | "judge", CSSProperties>> = {};
        for (const key of ["bull", "bear", "judge"] as const) {
          const node = debateNodeRefs.current[key]?.getBoundingClientRect();
          if (!node) continue;
          const deltaX = targetX - node.left;
          const deltaY = targetY - node.top;
          debateNext[key] = {
            width: `${Math.hypot(deltaX, deltaY).toFixed(1)}px`,
            transform: `rotate(${Math.atan2(deltaY, deltaX).toFixed(5)}rad)`,
          };
        }
        setDebateLinkGeometry(debateNext);
      });
    };

    const observer = new ResizeObserver(updateGeometry);
    if (commandDeckRef.current) observer.observe(commandDeckRef.current);
    if (reactorCoreRef.current) observer.observe(reactorCoreRef.current);
    for (const node of Object.values(researchNodeRefs.current)) {
      if (node) observer.observe(node);
    }
    for (const node of Object.values(debateNodeRefs.current)) {
      if (node) observer.observe(node);
    }
    window.addEventListener("resize", updateGeometry);
    updateGeometry();
    return () => {
      window.cancelAnimationFrame(animationFrame);
      window.removeEventListener("resize", updateGeometry);
      observer.disconnect();
    };
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

  const refreshProviderStatus = useCallback(async () => {
    if (!providerClient || !TIJORI_TARGET) return;
    setProviderCommand("status");
    setProviderNotice(null);
    try {
      setProviderLifecycle(await providerClient.status(TIJORI_TARGET));
    } catch (reason) {
      setProviderLifecycle(null);
      setProviderNotice(
        reason instanceof ProviderSessionClientError
          ? reason.message
          : "The provider connection status is unavailable.",
      );
    } finally {
      setProviderCommand(null);
    }
  }, [providerClient]);

  useEffect(() => {
    if (!providerClient || !TIJORI_TARGET) return;
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) void refreshProviderStatus();
    });
    return () => { cancelled = true; };
  }, [providerClient, refreshProviderStatus]);

  const connectProvider = useCallback(async () => {
    if (!providerClient || !TIJORI_TARGET || providerCommand) return;
    const replacing = providerLifecycle?.status === "expired" || providerLifecycle?.status === "revoked";
    const confirmed = window.confirm(
      replacing
        ? "Reconnect Tijori? Jarvis will open a headed Tijori login window and securely replace the expired or revoked local session."
        : "Connect Tijori? Jarvis will open a headed Tijori login window. Sign in directly with Tijori; Jarvis will never ask for or receive your credentials.",
    );
    if (!confirmed) return;
    setProviderCommand("provision");
    setProviderNotice("Waiting for you to complete the Tijori login window…");
    try {
      const lifecycle = await providerClient.provision(TIJORI_TARGET, {
        idempotencyKey: requestId(),
        replaceExisting: replacing,
      });
      setProviderLifecycle(lifecycle);
      setProviderNotice("Tijori research connection is ready.");
    } catch (reason) {
      setProviderNotice(
        reason instanceof ProviderSessionClientError
          ? reason.message
          : "Tijori connection could not be completed.",
      );
    } finally {
      setProviderCommand(null);
    }
  }, [providerClient, providerCommand, providerLifecycle]);

  const revokeProvider = useCallback(async () => {
    if (!providerClient || !TIJORI_TARGET || providerCommand) return;
    const confirmed = window.confirm(
      "Revoke the local Tijori session? Jarvis will securely delete the saved session artifact. You will need to sign in again for future fundamental refreshes.",
    );
    if (!confirmed) return;
    setProviderCommand("revoke");
    setProviderNotice("Securely revoking the local Tijori session…");
    try {
      const lifecycle = await providerClient.revoke(TIJORI_TARGET, {
        idempotencyKey: requestId(),
      });
      setProviderLifecycle(lifecycle);
      setProviderNotice("Tijori research connection has been securely revoked.");
    } catch (reason) {
      setProviderNotice(
        reason instanceof ProviderSessionClientError
          ? reason.message
          : "Tijori connection could not be revoked.",
      );
    } finally {
      setProviderCommand(null);
    }
  }, [providerClient, providerCommand]);

  useEffect(() => {
    let disposed = false;
    let created: CreatedSession | null = null;
    let bootstrapInFlight = false;
    let bootstrapAttempts = 0;
    let bootstrapRetry: number | null = null;

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

    const bootstrapSession = () => {
      if (disposed || created || bootstrapInFlight) return;
      bootstrapInFlight = true;
      bootstrapAttempts += 1;
      if (bootstrapAttempts > 1) {
        setNotice("Re-establishing secure research link…");
      }
      void fetch(`${API_BASE}/api/v1/sessions`, { method: "POST" })
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
          setError(null);
          setNotice("Neural link secure. Wake phrase required.");
        })
        .catch((reason) => {
          if (!disposed) {
            setError(reason instanceof Error ? reason.message : "Jarvis is offline.");
            setNotice("Research core unavailable · retrying automatically");
          }
        })
        .finally(() => {
          bootstrapInFlight = false;
          if (!disposed && !created) {
            bootstrapRetry = window.setTimeout(
              bootstrapSession,
              SESSION_BOOTSTRAP_RETRY_MS,
            );
          }
        });
    };

    bootstrapSession();
    return () => {
      disposed = true;
      if (bootstrapRetry !== null) window.clearTimeout(bootstrapRetry);
      window.removeEventListener("pagehide", handlePageHide);
      window.removeEventListener("pageshow", handlePageShow);
      conversationAbort.current?.abort();
      workflowAbort.current?.abort();
      closeCreatedSession(created);
    };
  }, []);

  const fetchCompletedOperation = useCallback(async (operationId: string) => {
    if (!session || !token) return;
    const operationBase = `${API_BASE}/api/v1/sessions/${session.session_id}/operations/${operationId}`;
    const [dashboardResponse, resultResponse] = await Promise.all([
      fetch(`${operationBase}/dashboard`, { headers: authHeaders }),
      fetch(`${operationBase}/result`, { headers: authHeaders }),
    ]);
    if (!dashboardResponse.ok) {
      throw new Error("Jarvis completed, but the dashboard projection is unavailable.");
    }
    if (!resultResponse.ok) {
      throw new Error("Jarvis completed, but the financial-document inventory is unavailable.");
    }
    const [nextDashboard, operationResult] = await Promise.all([
      dashboardResponse.json() as Promise<Dashboard>,
      resultResponse.json() as Promise<OperationResultResponse>,
    ]);
    setDashboard(nextDashboard);
    setStructuredDocuments(operationResult.output?.structured_document_references ?? []);
    setBenchmarkingReference(operationResult.output?.benchmarking_financials_reference ?? null);
    setActiveOperation(null);
  }, [authHeaders, session, token]);

  const inspectFinancialDocument = useCallback(async (
    reference: StructuredDocumentReference,
  ) => {
    if (!session || !token || !displayOperation) return;
    setFinancialDocumentLoading(reference.cache_entry_id);
    setFinancialDocumentError(null);
    try {
      const resolved = await fetchStructuredFinancialDocument({
        apiBase: API_BASE,
        sessionId: session.session_id,
        operationId: displayOperation,
        cacheEntryId: reference.cache_entry_id,
        accessToken: token,
      });
      setSelectedFinancialDocument(resolved);
    } catch (reason) {
      setFinancialDocumentError(
        reason instanceof Error
          ? reason.message
          : "The financial document is currently unavailable.",
      );
    } finally {
      setFinancialDocumentLoading(null);
    }
  }, [displayOperation, session, token]);

  const inspectBenchmarkingFinancials = useCallback(async () => {
    if (!session || !token || !displayOperation || !benchmarkingReference) return;
    setFinancialDocumentLoading(benchmarkingReference.cache_entry_id);
    setFinancialDocumentError(null);
    try {
      const resolved = await fetchBenchmarkingFinancials({
        apiBase: API_BASE,
        sessionId: session.session_id,
        operationId: displayOperation,
        cacheEntryId: benchmarkingReference.cache_entry_id,
        accessToken: token,
      });
      setSelectedBenchmarking(resolved);
    } catch (reason) {
      setFinancialDocumentError(
        reason instanceof Error
          ? reason.message
          : "The benchmarking matrix is currently unavailable.",
      );
    } finally {
      setFinancialDocumentLoading(null);
    }
  }, [benchmarkingReference, displayOperation, session, token]);

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
          neuralAudioRef.current?.play(
            matchesJudge(activity) || matchesBriefing(activity)
              ? "verdict"
              : matchesEvidence(activity)
                ? "evidence"
                : "dispatch",
          );
        }
        if (event === "terminal") {
          const terminal = data as OperationTerminal;
          setOperationStatus(terminal.status);
          if (terminal.status === "completed") {
            neuralAudioRef.current?.play("complete");
            fetchCompletedOperation(operationId).catch((reason) => setError(reason.message));
          } else {
            neuralAudioRef.current?.play("fault");
            setActiveOperation(null);
            if (
              terminal.failure
              && RECOVERABLE_INSTRUMENT_FAILURES.has(terminal.failure.code)
            ) {
              setError(null);
              setNotice("Jarvis is matching the company to a verified NSE instrument…");
            } else {
              setError(
                terminal.failure?.message
                  ?? (terminal.status === "cancelled"
                    ? "Research operation was cancelled."
                    : "Research operation failed without a safe explanation."),
              );
            }
          }
        }
        });
      })
      .catch((reason) => {
        if (reason.name !== "AbortError") {
          neuralAudioRef.current?.play("fault");
          setError(reason.message);
          setOperationStatus("failed");
        }
      });
  }, [authHeaders, fetchCompletedOperation, session, token]);

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
          if (item.to_state === "awaiting_confirmation") setError(null);
        }
        if (event === "conversation-terminal") {
          const snapshot = data as ConversationSnapshot;
          setConversation(snapshot);
          if (snapshot.display_message) setNotice(snapshot.display_message);
          if (snapshot.state === "awaiting_confirmation") setError(null);
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
    if (/^hey[\s,]+jarvis\b/i.test(normalized)) neuralAudioRef.current?.play("activation");
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
        setStructuredDocuments([]);
        setBenchmarkingReference(null);
        setSelectedFinancialDocument(null);
        setSelectedBenchmarking(null);
        setFinancialDocumentError(null);
        setActivities([]);
        setActiveOperation(operationId);
        setDisplayOperation(operationId);
        setOperationStatus("queued");
        streamWorkflow(operationId);
      }
    } catch (reason) {
      neuralAudioRef.current?.play("fault");
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
      if (!captureRequestedRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
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
    } catch (reason) {
      stopVoiceCapture();
      throw reason;
    }
  }, [stopVoiceCapture, uploadVoiceSegment]);

  useEffect(() => {
    if (!voiceInputEnabled || !session || !token) {
      captureRequestedRef.current = false;
      stopVoiceCapture();
      return;
    }
    captureRequestedRef.current = true;
    let cancelled = false;
    void startVoiceCapture().catch(() => {
      if (cancelled) return;
      setVoiceInputEnabled(false);
      setError("Microphone access was denied or unavailable.");
    });
    return () => {
      cancelled = true;
      captureRequestedRef.current = false;
      stopVoiceCapture();
    };
  }, [voiceInputEnabled, session, token, startVoiceCapture, stopVoiceCapture]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void sendText(command);
  };

  const progress = (matches: WorkflowMatcher) => workflowProgress(activities, matches);
  const evidenceState = progress(matchesEvidence);
  const judgeState = progress(matchesJudge);
  const briefingState = progress(matchesBriefing);
  const agentStates = {
    research: progress(matchesMarket),
    daily: progress(matchesDaily),
    weekly: progress(matchesWeekly),
    bull: progress(matchesBull),
    bear: progress(matchesBear),
    judge: progress((item) => matchesEvidence(item) || matchesJudge(item)),
  };
  const researchLink = agentStates.research.status === "active"
    ? {
      status: "active" as const,
      direction: "bidirectional" as const,
      label: "Preparing market data",
    }
    : agentStates.research.status === "complete"
      ? {
        // Keep the acknowledgement visible while downstream agents work. This
        // is presentation-only and never blocks the workflow event stream.
        status: "active" as const,
        direction: "to-jarvis" as const,
        label: "Market data ready",
      }
      : agentStates.research.status === "failed"
        ? {
          status: "failed" as const,
          direction: "to-jarvis" as const,
          label: "Data fault",
        }
        : {
          status: "standby" as const,
          direction: "to-agent" as const,
          label: "Dispatch",
        };
  const evidenceReturning = evidenceState.status === "active" || judgeState.status === "active";
  const judgeBriefing = briefingState.status === "active";
  const fundamentalDocumentCount = structuredDocuments.length + (benchmarkingReference ? 1 : 0);
  const fundamentalStatus: ProgressStatus = providerCommand
    ? "active"
    : fundamentalDocumentCount > 0
      ? "complete"
    : providerLifecycle?.status === "ready"
      ? "complete"
      : providerLifecycle?.status === "expired"
        ? "failed"
        : "standby";
  const fundamentalStatusLabel = providerCommand
    ? "Connecting"
    : fundamentalDocumentCount > 0
      ? `${fundamentalDocumentCount} documents ready`
    : providerLifecycle?.status === "ready"
      ? "Provider ready"
      : providerLifecycle?.status === "expired"
        ? "Reconnect"
        : "Standby";
  const fundamentalLink = providerCommand === "provision"
    ? {
      status: "active" as const,
      direction: "to-agent" as const,
      label: "Secure connection",
    }
    : providerCommand === "status"
      ? {
        status: "active" as const,
        direction: "bidirectional" as const,
        label: "Provider check",
      }
      : providerCommand === "revoke"
        ? {
          status: "active" as const,
          direction: "to-agent" as const,
          label: "Revoke access",
        }
        : fundamentalDocumentCount > 0
          ? {
            status: "active" as const,
            direction: "to-jarvis" as const,
            label: "Financial evidence ready",
          }
        : providerLifecycle?.status === "ready"
          ? {
            status: "active" as const,
            direction: "to-jarvis" as const,
            label: "Provider ready",
          }
          : providerLifecycle?.status === "expired"
            ? {
              status: "failed" as const,
              direction: "to-jarvis" as const,
              label: "Session expired",
            }
            : {
              status: "standby" as const,
              direction: "to-agent" as const,
              label: providerLifecycle?.status === "revoked" ? "Access revoked" : "Disconnected",
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

      <section ref={commandDeckRef} className="command-deck" aria-label="Jarvis research command center">
        <aside className="agent-column left-agents">
          <p className="section-label">Research division</p>
          <div ref={(node) => { researchNodeRefs.current.research = node; }} className="research-agent-node node-research">
            <AgentCard id="R" label="Market Data Analyst" specialty="Instrument & broker data" tone="cyan" status={agentStates.research.status} visual={AGENT_VISUALS.research} />
            <CommunicationLink
              agent="research"
              status={researchLink.status}
              direction="bidirectional"
              label={researchLink.label}
              style={researchLinkGeometry.research}
            />
          </div>
          <div ref={(node) => { researchNodeRefs.current.daily = node; }} className="research-agent-node node-daily">
            <AgentCard id="D" label="Daily Analyst" specialty="Tactical structure" tone="violet" status={agentStates.daily.status} visual={AGENT_VISUALS.daily} />
            <CommunicationLink
              agent="daily"
              status={evidenceReturning ? "active" : agentStates.daily.status}
              direction="bidirectional"
              label={evidenceReturning ? "Daily evidence" : "Dispatch"}
              style={researchLinkGeometry.daily}
            />
          </div>
          <div ref={(node) => { researchNodeRefs.current.weekly = node; }} className="research-agent-node node-weekly">
            <AgentCard id="W" label="Weekly Analyst" specialty="Strategic structure" tone="amber" status={agentStates.weekly.status} visual={AGENT_VISUALS.weekly} />
            <CommunicationLink
              agent="weekly"
              status={evidenceReturning ? "active" : agentStates.weekly.status}
              direction="bidirectional"
              label={evidenceReturning ? "Weekly evidence" : "Dispatch"}
              style={researchLinkGeometry.weekly}
            />
          </div>
          <div ref={(node) => { researchNodeRefs.current.fundamental = node; }} className="fundamental-agent-dock research-agent-node node-fundamental">
            <AgentCard
              id="F"
              label="Fundamental Analyst"
              specialty="Company fundamentals"
              tone="cyan"
              status={fundamentalStatus}
              statusLabel={fundamentalStatusLabel}
              visual={AGENT_VISUALS.financial}
            />
            <CommunicationLink
              agent="fundamental"
              status={fundamentalLink.status}
              direction="bidirectional"
              label={fundamentalLink.label}
              style={researchLinkGeometry.fundamental}
            />
            <section className="provider-session-panel" aria-label="Fundamental Analyst data-provider connection">
              <div className="provider-session-summary">
                <span className={`provider-dot ${providerLifecycle?.status ?? "unconfigured"}`} aria-hidden="true" />
                <div>
                  <p>Data provider · Tijori</p>
                  <strong>{providerCommand ?? providerLifecycle?.status ?? "unconfigured"}</strong>
                </div>
                {providerLifecycle?.expires_at && (
                  <small>
                    Expires {new Date(providerLifecycle.expires_at).toLocaleString("en-IN", {
                      timeZone: "Asia/Kolkata",
                    })} IST
                  </small>
                )}
              </div>
              {providerNotice && <p className="provider-session-notice" role="status">{providerNotice}</p>}
              {fundamentalDocumentCount > 0 && (
                <details className="financial-document-inventory">
                  <summary>
                    Completed financial documents
                    <b>{fundamentalDocumentCount}</b>
                  </summary>
                  <ul>
                    {structuredDocuments.map((document) => (
                      <li key={document.cache_entry_id}>
                        <span>
                          <strong>{FINANCIAL_DOCUMENT_LABELS[document.document_type]}</strong>
                          <small>{document.reporting_basis.replaceAll("_", " ")}</small>
                        </span>
                        <em className={document.source.toLowerCase()}>{document.source}</em>
                        <time dateTime={document.expires_at}>
                          expires {new Date(document.expires_at).toLocaleDateString("en-IN", {
                            timeZone: "Asia/Kolkata",
                          })}
                        </time>
                        <button
                          type="button"
                          onClick={() => void inspectFinancialDocument(document)}
                          disabled={financialDocumentLoading !== null}
                        >
                          {financialDocumentLoading === document.cache_entry_id
                            ? "Loading"
                            : "Inspect"}
                        </button>
                      </li>
                    ))}
                    {benchmarkingReference && (
                      <li key={benchmarkingReference.cache_entry_id}>
                        <span>
                          <strong>Peer benchmarking</strong>
                          <small>Financial comparison matrix</small>
                        </span>
                        <em className={benchmarkingReference.source.toLowerCase()}>{benchmarkingReference.source}</em>
                        <time dateTime={benchmarkingReference.expires_at}>
                          expires {new Date(benchmarkingReference.expires_at).toLocaleDateString("en-IN", {
                            timeZone: "Asia/Kolkata",
                          })}
                        </time>
                        <button
                          type="button"
                          onClick={() => void inspectBenchmarkingFinancials()}
                          disabled={financialDocumentLoading !== null}
                        >
                          {financialDocumentLoading === benchmarkingReference.cache_entry_id
                            ? "Loading"
                            : "Inspect"}
                        </button>
                      </li>
                    )}
                  </ul>
                </details>
              )}
              <div className="provider-session-actions">
                <button
                  type="button"
                  onClick={() => void refreshProviderStatus()}
                  disabled={!providerClient || Boolean(providerCommand)}
                >
                  Check status
                </button>
                {providerLifecycle?.status !== "ready" && (
                  <button
                    type="button"
                    className="primary"
                    onClick={() => void connectProvider()}
                    disabled={!providerClient || Boolean(providerCommand)}
                  >
                    {providerLifecycle?.status === "expired" || providerLifecycle?.status === "revoked"
                      ? "Reconnect"
                      : "Connect"}
                  </button>
                )}
                {providerLifecycle?.status === "ready" && (
                  <button
                    type="button"
                    className="danger"
                    onClick={() => void revokeProvider()}
                    disabled={Boolean(providerCommand)}
                  >
                    Revoke securely
                  </button>
                )}
              </div>
            </section>
          </div>
        </aside>

        <section className="reactor-zone">
          <div className={`reactor ${state} ${activeOperation ? "engaged" : ""}`}>
            <div className="orbit orbit-one"><i /><i /><i /><i /><i /></div>
            <div className="orbit orbit-two"><i /><i /><i /><i /><i /><i /></div>
            <div className="orbit orbit-three"><i /><i /><i /><i /><i /><i /><i /></div>
            <button
              ref={reactorCoreRef}
              className="reactor-core"
              onClick={state === "dormant" ? () => void sendText("Hey Jarvis") : undefined}
              aria-label={state === "dormant" ? "Wake Jarvis" : `Jarvis is ${state}`}
            >
              <span className="intelligence-core-sigil" aria-hidden="true">
                <i className="core-neural-ring" />
                <i className="core-energy-orb" />
                <i className="core-neural-node core-node-one" />
                <i className="core-neural-node core-node-two" />
                <i className="core-neural-node core-node-three" />
                <i className="core-neural-node core-node-four" />
                <i className="core-neural-node core-node-five" />
                <i className="core-neural-node core-node-six" />
              </span>
              <span className="core-state">{state}</span>
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
            <button
              type="button"
              className={`sound-switch ${soundEffectsEnabled ? "enabled" : "disabled"}`}
              role="switch"
              aria-checked={soundEffectsEnabled}
              onClick={() => {
                const enabled = !soundEffectsEnabled;
                setSoundEffectsEnabled(enabled);
                void neuralAudioRef.current?.setEnabled(enabled);
              }}
            >
              <span className="switch-led" aria-hidden="true" />
              <span>Neural audio</span>
              <span className="switch-track" aria-hidden="true"><i /></span>
              <b>{soundEffectsEnabled ? "ON" : "OFF"}</b>
            </button>
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
          <div ref={(node) => { debateNodeRefs.current.bull = node; }} className="debate-agent-node node-bull">
            <AgentCard id="♉" label="Bull" specialty="Constructive advocate" tone="amber" status={agentStates.bull.status} visual={AGENT_VISUALS.bull} />
            <CommunicationLink agent="bull" status={agentStates.bull.status} direction="bidirectional" label="Bull debate" style={debateLinkGeometry.bull} />
          </div>
          <div ref={(node) => { debateNodeRefs.current.bear = node; }} className="debate-agent-node node-bear">
            <AgentCard id="♙" label="Bear" specialty="Risk advocate" tone="rose" status={agentStates.bear.status} visual={AGENT_VISUALS.bear} />
            <CommunicationLink agent="bear" status={agentStates.bear.status} direction="bidirectional" label="Bear debate" style={debateLinkGeometry.bear} />
          </div>
          <div ref={(node) => { debateNodeRefs.current.judge = node; }} className="debate-agent-node node-judge">
            <AgentCard id="J" label="Senior Judge" specialty="Evidence synthesis" tone="cyan" status={agentStates.judge.status} visual={AGENT_VISUALS.judge} />
            <CommunicationLink
              agent="judge"
              status={judgeBriefing ? "active" : agentStates.judge.status}
              direction={judgeBriefing ? "to-jarvis" : "bidirectional"}
              label={judgeBriefing ? "Verdict" : "Synthesis"}
              style={debateLinkGeometry.judge}
            />
          </div>
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
          {financialDocumentError && (
            <p className="financial-document-error" role="alert">{financialDocumentError}</p>
          )}
          {selectedFinancialDocument && (
            <article className="financial-document-inspector glass-panel">
              <header>
                <div>
                  <p className="eyebrow">Fundamental Analyst · validated cached document</p>
                  <h2>{FINANCIAL_DOCUMENT_LABELS[selectedFinancialDocument.document.document_type]}</h2>
                  <p>
                    {selectedFinancialDocument.document.issuer.legal_name} · {selectedFinancialDocument.document.reporting_basis.replaceAll("_", " ")} · {selectedFinancialDocument.document.source_unit}
                  </p>
                </div>
                <button type="button" onClick={() => setSelectedFinancialDocument(null)}>Close</button>
              </header>
              <div className="financial-table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Financial line item</th>
                      {selectedFinancialDocument.document.periods
                        .slice()
                        .sort((left, right) => left.display_order - right.display_order)
                        .map((period) => <th key={period.period_key}>{period.source_label}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {selectedFinancialDocument.document.rows
                      .slice()
                      .sort((left, right) => left.display_order - right.display_order)
                      .map((row) => {
                        const cells = new Map(row.cells.map((cell) => [cell.period_key, cell]));
                        return (
                          <tr className={`row-${row.row_kind}`} key={row.row_key}>
                            <th style={{ paddingLeft: `${14 + row.depth * 18}px` }}>{row.original_label}</th>
                            {selectedFinancialDocument.document.periods
                              .slice()
                              .sort((left, right) => left.display_order - right.display_order)
                              .map((period) => {
                                const cell = cells.get(period.period_key);
                                return <td key={period.period_key}>{cell?.source_value ?? "—"}</td>;
                              })}
                          </tr>
                        );
                      })}
                  </tbody>
                </table>
              </div>
              <footer>
                <span>{selectedFinancialDocument.document.rows.length} rows · {selectedFinancialDocument.document.periods.length} periods</span>
                <span>Validated · fully expanded · expires {new Date(selectedFinancialDocument.document.expires_at).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" })}</span>
              </footer>
            </article>
          )}
          {selectedBenchmarking && (
            <article className="financial-document-inspector glass-panel">
              <header>
                <div>
                  <p className="eyebrow">Fundamental Analyst · validated cached matrix</p>
                  <h2>Peer benchmarking</h2>
                  <p>
                    {selectedBenchmarking.document.issuer.legal_name} · Financials · observed {new Date(`${selectedBenchmarking.document.observation_date}T00:00:00+05:30`).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" })}
                  </p>
                </div>
                <button type="button" onClick={() => setSelectedBenchmarking(null)}>Close</button>
              </header>
              <div className="financial-table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Benchmark metric</th>
                      {selectedBenchmarking.document.companies
                        .slice()
                        .sort((left, right) => left.display_order - right.display_order)
                        .map((company) => (
                          <th className={company.is_subject ? "subject-company" : undefined} key={company.company_key}>
                            {company.legal_name}{company.is_subject ? " · Subject" : ""}
                          </th>
                        ))}
                    </tr>
                  </thead>
                  <tbody>
                    {selectedBenchmarking.document.rows
                      .slice()
                      .sort((left, right) => left.display_order - right.display_order)
                      .map((row) => {
                        const cells = new Map(row.cells.map((cell) => [cell.company_key, cell]));
                        return (
                          <tr className={`row-${row.row_kind}`} key={row.row_key}>
                            <th style={{ paddingLeft: `${14 + row.depth * 18}px` }}>{row.original_label}</th>
                            {selectedBenchmarking.document.companies
                              .slice()
                              .sort((left, right) => left.display_order - right.display_order)
                              .map((company) => {
                                const cell = cells.get(company.company_key);
                                return (
                                  <td className={cell?.is_best ? "best-benchmark" : undefined} key={company.company_key}>
                                    {cell?.source_value ?? "—"}
                                  </td>
                                );
                              })}
                          </tr>
                        );
                      })}
                  </tbody>
                </table>
              </div>
              <footer>
                <span>{selectedBenchmarking.document.rows.length} metrics · {selectedBenchmarking.document.companies.length} companies</span>
                <span>Validated · complete matrix · expires {new Date(selectedBenchmarking.document.expires_at).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" })}</span>
              </footer>
            </article>
          )}
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
