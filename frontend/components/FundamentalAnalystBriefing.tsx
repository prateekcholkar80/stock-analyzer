import type {
  FundamentalAnalysis,
  FundamentalPackageReference,
} from "@/lib/fundamental-analysis";
import { fundamentalConfidenceTone } from "@/lib/fundamental-analysis";

function FindingList({ items, empty }: { items: string[]; empty: string }) {
  return items.length > 0
    ? <ul>{items.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul>
    : <p className="fundamental-empty">{empty}</p>;
}

export function FundamentalAnalystBriefing({
  analysis,
  packageReference,
}: {
  analysis: FundamentalAnalysis;
  packageReference: FundamentalPackageReference | null;
}) {
  const confidenceTone = fundamentalConfidenceTone(
    analysis.confidence_percentage,
  );

  return (
    <article
      className="fundamental-briefing glass-panel"
      aria-labelledby="fundamental-briefing-title"
    >
      <header className="fundamental-briefing-heading">
        <div>
          <p className="eyebrow">Fundamental Analyst · grounded briefing</p>
          <h2 id="fundamental-briefing-title">Financial health snapshot</h2>
          <small>
            Generated {new Date(analysis.generated_at).toLocaleString("en-IN", {
              timeZone: "Asia/Kolkata",
            })} IST
          </small>
        </div>
        <div className={`fundamental-confidence ${confidenceTone}`}>
          <span>Evidence confidence</span>
          <strong>{analysis.confidence_percentage.toFixed(0)}%</strong>
          <small>{confidenceTone}</small>
        </div>
      </header>

      <div className="fundamental-summary-grid">
        <section>
          <span>Coverage &amp; freshness</span>
          <p>{analysis.evidence_coverage_and_freshness}</p>
          {packageReference && (
            <p
              className="fundamental-runtime"
              title={`${packageReference.package_id} · manifest ${packageReference.manifest_fingerprint}`}
            >
              Committed package {packageReference.package_id.slice(-12)}
              {" · "}manifest {packageReference.manifest_fingerprint.slice(0, 12)}…
              {" · "}valid until {new Date(packageReference.expires_at).toLocaleString("en-IN", {
                timeZone: "Asia/Kolkata",
              })} IST
            </p>
          )}
        </section>
        <section>
          <span>Preliminary financial health</span>
          <p>{analysis.preliminary_financial_health}</p>
        </section>
      </div>

      <div className="fundamental-findings-grid">
        <section className="strengths">
          <h3>Financial strengths</h3>
          <FindingList
            items={analysis.financial_strengths}
            empty="No specific strength was established from the available evidence."
          />
        </section>
        <section className="risks">
          <h3>Risks &amp; conflicts</h3>
          <FindingList
            items={analysis.financial_risks_and_conflicts}
            empty="No material conflict was identified in the reviewed documents."
          />
        </section>
        <section className="valuation">
          <h3>Valuation observations</h3>
          <FindingList
            items={analysis.valuation_observations}
            empty="The available evidence did not support a valuation conclusion."
          />
        </section>
      </div>

      <footer className="fundamental-briefing-footer">
        <details>
          <summary>
            Limitations · {analysis.missing_evidence_and_limitations.length}
          </summary>
          <FindingList
            items={analysis.missing_evidence_and_limitations}
            empty="No additional limitation was reported."
          />
        </details>
        <details>
          <summary>
            Evidence lineage · {analysis.document_citations.length} documents
          </summary>
          <ul className="fundamental-citations">
            {analysis.document_citations.map((citation) => (
              <li key={citation}>{citation}</li>
            ))}
          </ul>
          <p className="fundamental-runtime">
            Skill {analysis.runtime_skill_id} v{analysis.runtime_skill_version}
            {" · "}Model {analysis.model_id}
          </p>
        </details>
      </footer>
    </article>
  );
}
