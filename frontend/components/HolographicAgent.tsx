import {
  agentVisualState,
  type AgentVisualSpec,
} from "@/lib/agent-visuals";
import type { ProgressStatus } from "@/lib/workflow-progress";

function BullReactorSigil() {
  return (
    <span className="kinetic-sigil bull-reactor-sigil" aria-hidden="true">
      <i className="sigil-ring" />
      <i className="sigil-core" />
      <i className="bull-horn horn-left" />
      <i className="bull-horn horn-right" />
      <i className="bull-vector" />
    </span>
  );
}

function BearReactorSigil() {
  return (
    <span className="kinetic-sigil bear-reactor-sigil" aria-hidden="true">
      <i className="sigil-ring" />
      <i className="sigil-core" />
      <span className="bear-claw-field">
        <i className="bear-claw claw-one" />
        <i className="bear-claw claw-two" />
        <i className="bear-claw claw-three" />
      </span>
    </span>
  );
}

function JudgeReactorSigil() {
  return (
    <span className="kinetic-sigil judge-reactor-sigil" aria-hidden="true">
      <i className="sigil-ring" />
      <span className="hologram-balance">
        <i className="balance-crown" />
        <i className="balance-beam" />
        <i className="balance-stand" />
        <i className="balance-base" />
        <i className="balance-chain chain-left" />
        <i className="balance-chain chain-right" />
        <i className="balance-pan pan-left" />
        <i className="balance-pan pan-right" />
      </span>
    </span>
  );
}

function CandlestickRain() {
  return (
    <span className="candlestick-rain">
      <i className="market-candle candle-green candle-one" />
      <i className="market-candle candle-red candle-two" />
      <i className="market-candle candle-green candle-three" />
      <i className="market-candle candle-red candle-four" />
      <i className="market-candle candle-green candle-five" />
      <i className="market-candle candle-red candle-six" />
    </span>
  );
}

function MarketDataScannerSigil() {
  return (
    <span className="kinetic-sigil market-data-scanner-sigil" aria-hidden="true">
      <i className="market-scan-ring scan-ring-outer" />
      <i className="market-scan-ring scan-ring-inner" />
      <i className="market-scan-crosshair" />
      <i className="market-scan-beam" />
      <CandlestickRain />
    </span>
  );
}

function DailyCandleLensSigil() {
  return (
    <span className="kinetic-sigil daily-candle-lens-sigil" aria-hidden="true">
      <i className="market-scan-ring scan-ring-outer" />
      <i className="market-scan-ring scan-ring-inner" />
      <i className="market-scan-crosshair" />
      <CandlestickRain />
      <i className="daily-analysis-lens" />
    </span>
  );
}

function WeeklyCandleSpectaclesSigil() {
  return (
    <span className="kinetic-sigil weekly-candle-specs-sigil" aria-hidden="true">
      <i className="market-scan-ring scan-ring-outer" />
      <i className="market-scan-ring scan-ring-inner" />
      <i className="market-scan-crosshair" />
      <CandlestickRain />
      <span className="weekly-analysis-specs">
        <i className="spec-lens spec-lens-left" />
        <i className="spec-lens spec-lens-right" />
        <i className="spec-bridge" />
        <i className="spec-arm spec-arm-left" />
        <i className="spec-arm spec-arm-right" />
      </span>
    </span>
  );
}

function FundamentalRadarSigil() {
  return (
    <span className="kinetic-sigil fundamental-radar-sigil" aria-hidden="true">
      <i className="radar-ring radar-ring-outer" />
      <i className="radar-ring radar-ring-inner" />
      <i className="radar-crosshair" />
      <i className="radar-sweep-beam" />
      <i className="radar-plane" />
      <i className="radar-blip blip-one" />
      <i className="radar-blip blip-two" />
      <span className="binary-rain">
        <b>1</b><b>0</b><b>1</b><b>1</b><b>0</b><b>0</b>
      </span>
    </span>
  );
}

function OrbitalPacketCluster() {
  return (
    <span className="orbital-packet-field" aria-hidden="true">
      <span className="packet-orbit packet-orbit-outer">
        <i /><i /><i /><i />
      </span>
      <span className="packet-orbit packet-orbit-inner">
        <i /><i /><i />
      </span>
    </span>
  );
}

export function HolographicAgent({
  spec,
  status,
}: {
  spec: AgentVisualSpec;
  status: ProgressStatus;
}) {
  const visualState = agentVisualState(status);

  return (
    <div
      className={`agent-hologram ${spec.tone} ${visualState} motif-${spec.motif}`}
      role="img"
      aria-label={`${spec.ariaLabel}: ${visualState}`}
    >
      <span className="hologram-grid" aria-hidden="true" />
      <span className="hologram-orbit orbit-outer" aria-hidden="true" />
      <span className="hologram-orbit orbit-inner" aria-hidden="true" />
      <span className="hologram-sweep" aria-hidden="true" />
      <OrbitalPacketCluster />
      {spec.key === "bull"
        ? <BullReactorSigil />
        : spec.key === "bear"
          ? <BearReactorSigil />
          : spec.key === "research"
            ? <MarketDataScannerSigil />
          : spec.key === "daily"
            ? <DailyCandleLensSigil />
          : spec.key === "weekly"
            ? <WeeklyCandleSpectaclesSigil />
          : spec.key === "judge"
            ? <JudgeReactorSigil />
          : spec.key === "financial"
            ? <FundamentalRadarSigil />
        : <span className={`hologram-render render-${spec.key}`} aria-hidden="true" />}
      <span className="hologram-particle particle-one" aria-hidden="true" />
      <span className="hologram-particle particle-two" aria-hidden="true" />
      <span className="hologram-particle particle-three" aria-hidden="true" />
    </div>
  );
}
