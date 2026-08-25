import type { Dashboard } from "@/lib/dashboard";
import {
  beginnerTimeframeSummary,
  judgeDecisionView,
  readableBriefingPoints,
} from "@/lib/executive-briefing";

function BriefingSection({
  eyebrow,
  title,
  points,
}: {
  eyebrow: string;
  title: string;
  points: string[];
}) {
  if (points.length === 0) return null;
  return (
    <section className="briefing-section">
      <p className="eyebrow">{eyebrow}</p>
      <h3>{title}</h3>
      <ul>
        {points.map((point, index) => <li key={`${title}-${index}`}>{point}</li>)}
      </ul>
    </section>
  );
}

export function ExecutiveBriefing({ dashboard }: { dashboard: Dashboard }) {
  const decision = judgeDecisionView(
    dashboard.debate.winner,
    dashboard.debate.confidence_percentage,
    dashboard.trade_plan.disposition,
  );
  const presentation = dashboard.presentation;
  const executivePoints = readableBriefingPoints(
    presentation?.executive_briefing
      ?? presentation?.summary
      ?? dashboard.debate.judge_rationale,
  );
  const weeklyPoints = readableBriefingPoints(presentation?.weekly_analysis);
  const dailyPoints = readableBriefingPoints(presentation?.daily_analysis);
  const judgePoints = readableBriefingPoints(
    presentation?.judge_conclusion_explanation ?? dashboard.debate.judge_rationale,
  );
  const changeConditions = dashboard.interpretation?.decision_change_conditions ?? [];
  const dailyStance = dashboard.interpretation?.daily.market_condition
    ?? dashboard.daily.stance;
  const weeklyStance = dashboard.interpretation?.weekly.market_condition
    ?? dashboard.weekly.stance;

  return (
    <article className={`executive-dossier glass-panel decision-${decision.tone}`}>
      <header className="executive-dossier-heading">
        <div>
          <p className="eyebrow">Chief executive briefing</p>
          <h2>Jarvis conclusion</h2>
        </div>
        <div className="executive-decision" aria-label="Judge decision">
          <strong>{decision.actionLabel}</strong>
          <span>{decision.verdictLabel}</span>
          <em>{decision.confidenceLabel}</em>
        </div>
      </header>

      <section className="executive-summary" aria-labelledby="executive-summary-title">
        <div>
          <p className="eyebrow">Start here</p>
          <h3 id="executive-summary-title">Executive summary</h3>
        </div>
        <ul>
          <li><b>Decision:</b> {decision.verdictLabel} · {decision.actionLabel}.</li>
          <li>
            <b>What {dashboard.debate.confidence_percentage.toFixed(0)}% means:</b>{" "}
            {decision.confidenceMeaning}
          </li>
          <li><b>Broader trend:</b> {beginnerTimeframeSummary("Weekly", weeklyStance)}</li>
          <li><b>Entry timing:</b> {beginnerTimeframeSummary("Daily", dailyStance)}</li>
        </ul>
      </section>

      <div className="briefing-grid">
        <BriefingSection
          eyebrow="Jarvis's concise read"
          title="Why this is the decision"
          points={executivePoints}
        />
        <BriefingSection
          eyebrow="Weekly analyst"
          title="The bigger picture"
          points={weeklyPoints.length ? weeklyPoints : [beginnerTimeframeSummary("Weekly", weeklyStance)]}
        />
        <BriefingSection
          eyebrow="Daily analyst"
          title="Near-term entry timing"
          points={dailyPoints.length ? dailyPoints : [beginnerTimeframeSummary("Daily", dailyStance)]}
        />
        <BriefingSection
          eyebrow="Senior Judge"
          title="How the evidence was weighed"
          points={judgePoints}
        />
        <BriefingSection
          eyebrow="Watch list"
          title="What could change this decision"
          points={changeConditions}
        />
      </div>

      <section className={`executive-trade-plan ${dashboard.trade_plan.disposition}`}>
        <div>
          <p className="eyebrow">Trade protocol</p>
          <strong>
            {dashboard.trade_plan.disposition === "actionable" ? "BUY SETUP" : "NO TRADE"}
          </strong>
        </div>
        {dashboard.trade_plan.disposition === "actionable" ? (
          <dl>
            <div><dt>Entry</dt><dd>₹{dashboard.trade_plan.entry_price?.toFixed(2)}</dd></div>
            <div><dt>Stop loss</dt><dd>₹{dashboard.trade_plan.stop_loss_price?.toFixed(2)}</dd></div>
            <div><dt>Target · 1:2</dt><dd>₹{dashboard.trade_plan.target_2r_price?.toFixed(2)}</dd></div>
            <div><dt>Target · 1:3</dt><dd>₹{dashboard.trade_plan.target_3r_price?.toFixed(2)}</dd></div>
          </dl>
        ) : (
          <p>{dashboard.trade_plan.rationale}</p>
        )}
      </section>

      {presentation?.limitations?.length ? (
        <details className="briefing-limitations">
          <summary>Scope and limitations</summary>
          <ul>{presentation.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
        </details>
      ) : null}
    </article>
  );
}
