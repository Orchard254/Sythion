import type { MorningBrief } from '../types';

/**
 * Read-only morning brief, delivered as a proactive message from the Core.
 * Purely informational: no control here can approve, grant, or clear anything.
 */
export function MorningBriefCard({ brief }: { brief: MorningBrief }) {
  return (
    <section className="notice notice--info" role="status" aria-label="Morning brief" data-testid="morning-brief-card">
      <strong>{brief.headline}</strong>
      <div className="briefing__list">
        {brief.sections.map((section) => (
          <div key={section.key} className="briefing__item">
            <span className="briefing__title">{section.title}</span>
            {section.lines.map((line, index) => (
              <span key={index} className="briefing__reason">{line}</span>
            ))}
          </div>
        ))}
      </div>
      <p className="briefing__note">{brief.note}</p>
    </section>
  );
}
