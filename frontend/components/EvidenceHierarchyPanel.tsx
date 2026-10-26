import type { HierarchicalEvidenceAssessment } from "@/lib/dashboard";
import {
  CORRELATION_GROUP_LABELS,
  EVIDENCE_FAMILY_LABELS,
  HIERARCHY_STATE_META,
  hierarchyReleaseLabel,
} from "@/lib/evidence-hierarchy";

export function EvidenceHierarchyPanel({
  assessment,
}: {
  assessment: HierarchicalEvidenceAssessment;
}) {
  const primary = HIERARCHY_STATE_META[assessment.primary_condition];
  const releaseTone = assessment.release_eligible ? "positive" : "negative";

  return (
    <article className="evidence-hierarchy glass-panel" aria-labelledby="evidence-hierarchy-title">
      <header className="hierarchy-heading">
        <div>
          <p className="eyebrow">Deterministic evidence hierarchy</p>
          <h2 id="evidence-hierarchy-title">Why the setup did—or did not—qualify</h2>
        </div>
        <div className={`hierarchy-release ${releaseTone}`}>
          <span>Trade release</span>
          <strong>{hierarchyReleaseLabel(assessment)}</strong>
        </div>
      </header>

      <div className="hierarchy-grid">
        <section className={`hierarchy-primary ${primary.tone}`}>
          <span>Primary context</span>
          <strong>{primary.label}</strong>
          <p>Market structure and price location determine the regime.</p>
        </section>

        <section className="hierarchy-groups">
          <div className="hierarchy-section-heading">
            <span>Correlated confirmation</span>
            <small>Counted once per group</small>
          </div>
          <div className="hierarchy-items">
            {assessment.supporting_groups.map((group) => {
              const state = HIERARCHY_STATE_META[group.condition];
              return (
                <div className={`hierarchy-item ${state.tone}`} key={group.correlation_group}>
                  <span>{CORRELATION_GROUP_LABELS[group.correlation_group]}</span>
                  <strong>{state.label}</strong>
                  <small>
                    {group.families.map((family) => EVIDENCE_FAMILY_LABELS[family]).join(" · ")}
                  </small>
                </div>
              );
            })}
          </div>
        </section>

        <section className="hierarchy-gates">
          <div className="hierarchy-section-heading">
            <span>Release gates</span>
            <small>Every applicable gate must pass</small>
          </div>
          <div className="hierarchy-items">
            {assessment.gate_states.map((gate) => {
              const state = HIERARCHY_STATE_META[gate.state];
              return (
                <div className={`hierarchy-item ${state.tone}`} key={gate.family}>
                  <span>{EVIDENCE_FAMILY_LABELS[gate.family]}</span>
                  <strong>{state.label}</strong>
                  <small>{gate.rationale}</small>
                </div>
              );
            })}
          </div>
        </section>
      </div>

      <footer className="hierarchy-footer">
        <div className={assessment.blocking_families.length ? "hierarchy-blockers active" : "hierarchy-blockers"}>
          <span>Blocking families</span>
          <strong>
            {assessment.blocking_families.length
              ? assessment.blocking_families.map((family) => EVIDENCE_FAMILY_LABELS[family]).join(" · ")
              : "None"}
          </strong>
        </div>
        {assessment.not_applicable_families.length > 0 && (
          <div className="hierarchy-scope">
            <span>Outside this workflow</span>
            <strong>
              {assessment.not_applicable_families
                .map((family) => EVIDENCE_FAMILY_LABELS[family])
                .join(" · ")}
            </strong>
          </div>
        )}
        <details className="hierarchy-lineage">
          <summary>Decision rationale and lineage · {assessment.evidence_ids.length} evidence IDs</summary>
          <p>{assessment.rationale}</p>
          {assessment.evidence_ids.length > 0 && (
            <ul>
              {assessment.evidence_ids.map((evidenceId) => <li key={evidenceId}>{evidenceId}</li>)}
            </ul>
          )}
        </details>
      </footer>
    </article>
  );
}
