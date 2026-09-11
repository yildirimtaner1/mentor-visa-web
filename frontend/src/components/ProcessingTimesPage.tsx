import { useState, type FC } from 'react';
import { SEO } from './common/SEO';
import data from '../data/processingTimes.json';
import './ProcessingTimes.css';

/**
 * Public, content-rich SEO landing page for Express Entry processing-time queries
 * ("Express Entry processing time", "AOR to PPR", "how long after biometrics", etc.).
 * Content is rendered from a static dataset (data/processingTimes.json) so it is fully
 * crawler-visible. The interactive, personalized tracker is the CTA.
 */

type Stat = { label: string; n: number; p25: number; median: number; p75: number };
const ALL = data.all as Record<string, Stat>;
const BY_STREAM = data.by_stream as Record<string, Record<string, Stat>>;
const TOTAL = data.total_cases as number;

// Column order for the table (only streams we actually have)
const STREAMS = ['CEC', 'FSW-Outland', 'PNP-Inland', 'FSW-Inland'].filter(s => BY_STREAM[s]);
const ROWS: { key: string; label: string }[] = [
  { key: 'aor_to_bil', label: 'AOR → Biometrics' },
  { key: 'aor_to_meds', label: 'AOR → Medical passed' },
  { key: 'aor_to_decision', label: 'AOR → Final decision' },
  { key: 'aor_to_p1', label: 'AOR → P1 (PR Portal, inland)' },
  { key: 'aor_to_ppr', label: 'AOR → PPR / Portal 2' },
  { key: 'aor_to_ecopr', label: 'AOR → eCOPR' },
];

const cec = (k: string) => BY_STREAM['CEC']?.[k];

// FAQ — single source for the visible Q&A and the FAQPage JSON-LD.
const FAQ: { q: string; a: string }[] = [
  {
    q: 'How long does Express Entry take after AOR in 2026?',
    a: `Across ${TOTAL} recent Express Entry cases, the median time from AOR (Acknowledgement of Receipt) to PPR / Portal 2 is about ${ALL.aor_to_ppr?.median} days, and to eCOPR about ${ALL.aor_to_ecopr?.median} days. CEC applicants tend to run a little longer (median ${cec('aor_to_ppr')?.median} days to PPR/Portal 2).`,
  },
  {
    q: 'How long after biometrics is PPR for Express Entry?',
    a: `Biometrics are usually requested about ${ALL.aor_to_bil?.median} days after AOR (CEC median ${cec('aor_to_bil')?.median} days; outland applicants are faster). PPR / Portal 2 then typically follows by around the ${ALL.aor_to_ppr?.median}-day-from-AOR mark.`,
  },
  {
    q: 'What is the AOR-to-PPR time for a CEC application?',
    a: `For Canadian Experience Class, the median AOR → PPR / Portal 2 is about ${cec('aor_to_ppr')?.median} days, with most cases falling between ${cec('aor_to_ppr')?.p25} and ${cec('aor_to_ppr')?.p75} days.`,
  },
  {
    q: 'How long does the background check and final decision take?',
    a: `The median time from AOR to a final decision is about ${ALL.aor_to_decision?.median} days (CEC median ${cec('aor_to_decision')?.median} days). Background checks ("ghost updates") often change without an email notification.`,
  },
  {
    q: 'When should I submit a webform or CSE to IRCC?',
    a: `A good rule of thumb is to wait until your case is clearly past the typical range before raising a webform — for example, if biometrics still have not been requested well beyond the ${cec('aor_to_bil')?.p75}-day mark for CEC, or if there is no movement past 6 months. Our tracker flags when your case is slower than 90% of similar applicants.`,
  },
];

const FAQ_SCHEMA = JSON.stringify({
  '@context': 'https://schema.org',
  '@type': 'FAQPage',
  mainEntity: FAQ.map(f => ({
    '@type': 'Question',
    name: f.q,
    acceptedAnswer: { '@type': 'Answer', text: f.a },
  })),
});

const fmtRange = (s?: Stat) => (s ? `${s.median}d` : '—');

// ─── "What changed" — deltas vs the previous monthly update ─────────────────────
const PREV = (data as any).previous as { generated: string; medians: Record<string, number> } | undefined;

const WhatChanged: FC = () => {
  if (!PREV) return null;
  const moves = ROWS
    .map(r => ({ label: r.label, delta: ALL[r.key] && PREV.medians[r.key] != null ? ALL[r.key]!.median - PREV.medians[r.key] : null }))
    .filter((m): m is { label: string; delta: number } => m.delta !== null && Math.abs(m.delta) >= 2);
  if (!moves.length) return null;
  return (
    <div style={{ background: '#fff', border: '1px solid var(--border-color)', borderRadius: 14, padding: '16px 20px' }}>
      <div style={{ fontSize: '0.72rem', fontWeight: 800, letterSpacing: '0.8px', color: '#6366f1', textTransform: 'uppercase', marginBottom: 8 }}>
        📈 What changed since our {PREV.generated} update
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {moves.map(m => (
          <span key={m.label} style={{
            fontSize: '0.76rem', fontWeight: 700, padding: '5px 12px', borderRadius: 999,
            background: m.delta > 0 ? '#fffbeb' : '#ecfdf5',
            border: `1px solid ${m.delta > 0 ? '#fde68a' : '#6ee7b7'}`,
            color: m.delta > 0 ? '#92400e' : '#047857',
          }}>
            {m.label}: {m.delta > 0 ? `+${m.delta}d slower` : `${m.delta}d faster`}
          </span>
        ))}
      </div>
      <p style={{ fontSize: '0.68rem', color: 'var(--text-muted)', margin: '8px 0 0' }}>
        Median shifts from our latest monthly data refresh — timelines have been trending {moves.filter(m => m.delta > 0).length >= moves.length / 2 ? 'slower' : 'faster'} recently.
      </p>
    </div>
  );
};

// ─── "The Typical Journey" dot-and-bar chart (cumulative days since AOR) ────────
const ACCENT = '#6366f1', ACCENT_SOFT = '#e0e7ff', GOLD = '#f59e0b', GOLD_SOFT = '#fde68a';
const JOURNEY_ROWS: { key: string; label: string }[] = [
  { key: 'aor_to_bil', label: 'BIL' },
  { key: 'aor_to_meds', label: 'Medical' },
  { key: 'aor_to_decision', label: 'Final Decision' },
  { key: 'aor_to_p1', label: 'P1 (inland)' },
  { key: 'aor_to_ppr', label: 'P2 / PPR' },
  { key: 'aor_to_ecopr', label: 'eCOPR' },
];
const JOURNEY_TABS = ['All', ...STREAMS];

const TypicalJourney: FC = () => {
  const [tab, setTab] = useState('All');
  const stats = tab === 'All' ? ALL : BY_STREAM[tab];
  const rows = JOURNEY_ROWS.filter(r => stats?.[r.key]);
  if (!rows.length) return null;
  // Headline uses the deepest milestone available for this stream.
  const last = stats[rows[rows.length - 1].key]!;
  const lastLabel = rows[rows.length - 1].label;
  const axisMax = Math.max(60, Math.ceil(Math.max(...rows.map(r => stats[r.key]!.p75)) / 30) * 30 + 30);
  const ticks = Array.from({ length: axisMax / 30 + 1 }, (_, i) => i * 30);
  const pct = (d: number) => `${Math.min(100, (d / axisMax) * 100)}%`;

  return (
    <div style={{ background: '#fff', border: '1px solid var(--border-color)', borderRadius: 16, padding: '24px 22px' }}>
      <div style={{ fontSize: '0.72rem', fontWeight: 800, letterSpacing: '0.8px', color: ACCENT, textTransform: 'uppercase', marginBottom: 6 }}>
        The typical journey{tab !== 'All' ? ` — ${tab}` : ''}
      </div>
      <h3 style={{ fontSize: 'clamp(1.15rem, 4vw, 1.5rem)', fontWeight: 800, margin: '0 0 4px' }}>
        AOR to {lastLabel} takes a median of <span style={{ color: GOLD }}>{last.median} days</span>
      </h3>
      <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', margin: '0 0 6px' }}>
        Half of applicants land between <strong>{last.p25}</strong> and <strong>{last.p75}</strong> days · n={last.n}
      </p>
      {/* Stream tabs */}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', margin: '12px 0 18px' }}>
        {JOURNEY_TABS.map(t => (
          <button key={t} onClick={() => setTab(t)} style={{
            padding: '6px 13px', borderRadius: 999, fontSize: '0.78rem', fontWeight: 700, cursor: 'pointer',
            border: tab === t ? `1.5px solid ${ACCENT}` : '1px solid var(--border-color)',
            background: tab === t ? ACCENT_SOFT : '#fff', color: tab === t ? '#4338ca' : 'var(--text-muted)',
          }}>{t}</button>
        ))}
      </div>
      {/* Legend */}
      <div style={{ display: 'flex', gap: 16, justifyContent: 'flex-end', fontSize: '0.7rem', color: 'var(--text-muted)', marginBottom: 8 }}>
        <span><span style={{ display: 'inline-block', width: 22, height: 8, borderRadius: 4, background: ACCENT_SOFT, verticalAlign: 'middle', marginRight: 5 }} />25th–75th %ile</span>
        <span><span style={{ display: 'inline-block', width: 9, height: 9, borderRadius: '50%', background: ACCENT, verticalAlign: 'middle', marginRight: 5 }} />median</span>
      </div>
      {/* Rows */}
      <div>
        {rows.map((r, i) => {
          const s = stats[r.key]!;
          const isFinal = i === rows.length - 1;
          return (
            <div key={r.key} style={{ display: 'grid', gridTemplateColumns: 'minmax(84px, 110px) 1fr 44px', alignItems: 'center', gap: 10, padding: '10px 0' }}>
              <div>
                <div style={{ fontSize: '0.8rem', fontWeight: 700, color: isFinal ? GOLD : 'var(--text-main, #1e293b)' }}>{r.label}</div>
                <div style={{ fontSize: '0.66rem', color: 'var(--text-muted)' }}>n={s.n}</div>
              </div>
              <div style={{ position: 'relative', height: 12 }}>
                <div style={{ position: 'absolute', inset: '2px 0', borderRadius: 6, background: '#f1f5f9' }} />
                <div style={{ position: 'absolute', top: 2, bottom: 2, left: pct(s.p25), width: `calc(${pct(s.p75)} - ${pct(s.p25)})`, borderRadius: 6, background: isFinal ? GOLD_SOFT : ACCENT_SOFT }} />
                <div style={{ position: 'absolute', top: '50%', left: pct(s.median), transform: 'translate(-50%, -50%)', width: 11, height: 11, borderRadius: '50%', background: isFinal ? GOLD : ACCENT, border: '2px solid #fff', boxShadow: '0 0 0 1px rgba(0,0,0,0.06)' }} />
              </div>
              <div style={{ fontSize: '0.88rem', fontWeight: 800, color: isFinal ? GOLD : 'var(--text-main, #1e293b)', textAlign: 'right' }}>{s.median}<span style={{ fontSize: '0.66rem', fontWeight: 600, color: 'var(--text-muted)' }}>d</span></div>
            </div>
          );
        })}
      </div>
      {/* Axis */}
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(84px, 110px) 1fr 44px', gap: 10, marginTop: 2 }}>
        <div />
        <div style={{ position: 'relative', height: 16, borderTop: '1px solid var(--border-color)' }}>
          {ticks.map(t => (
            <span key={t} style={{ position: 'absolute', left: pct(t), transform: 'translateX(-50%)', fontSize: '0.64rem', color: 'var(--text-muted)', top: 3 }}>{t}</span>
          ))}
        </div>
        <div style={{ fontSize: '0.64rem', color: 'var(--text-muted)', alignSelf: 'end' }}>days</div>
      </div>
      <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: 14, marginBottom: 0, lineHeight: 1.55 }}>
        Cumulative days since AOR. The dot is the median; the bar spans the typical (25th–75th percentile) range.
        Longer gaps between dots mean that leg of the process tends to take longer.
      </p>
    </div>
  );
};

// ─── "Community snapshot" — furthest reported stage across tracked journeys ─────
const SNAP = (data as any).snapshot as { stages: Record<string, number>; inland_pct: number } | undefined;
const SNAP_ROWS: { key: string; label: string; final?: boolean }[] = [
  { key: 'aor', label: 'AOR received' },
  { key: 'bil', label: 'BIL' },
  { key: 'medical', label: 'Medical' },
  { key: 'bg_check', label: 'BG Check' },
  { key: 'decision', label: 'Final Decision' },
  { key: 'p1_ppr', label: 'Portal / PPR' },
  { key: 'ecopr', label: 'eCOPR/COPR', final: true },
];

const CommunitySnapshot: FC = () => {
  if (!SNAP) return null;
  const max = Math.max(...Object.values(SNAP.stages));
  return (
    <div style={{ background: '#fff', border: '1px solid var(--border-color)', borderRadius: 16, padding: '24px 22px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: '0.72rem', fontWeight: 800, letterSpacing: '0.8px', color: ACCENT, textTransform: 'uppercase', marginBottom: 6 }}>Community snapshot</div>
          <h3 style={{ fontSize: 'clamp(1.15rem, 4vw, 1.5rem)', fontWeight: 800, margin: '0 0 4px' }}>Where tracked applicants are right now</h3>
          <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', margin: 0 }}>Each applicant's furthest reported stage, across {TOTAL} tracked journeys.</p>
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <span style={{ fontSize: '0.72rem', fontWeight: 700, padding: '4px 11px', borderRadius: 999, background: ACCENT_SOFT, color: '#4338ca' }}>{SNAP.inland_pct}% inland</span>
          <span style={{ fontSize: '0.72rem', fontWeight: 700, padding: '4px 11px', borderRadius: 999, background: '#f1f5f9', color: 'var(--text-muted)' }}>{100 - SNAP.inland_pct}% outland</span>
        </div>
      </div>
      <div style={{ marginTop: 18 }}>
        {SNAP_ROWS.map(r => {
          const n = SNAP.stages[r.key] ?? 0;
          const pctOfTotal = TOTAL ? (100 * n / TOTAL) : 0;
          return (
            <div key={r.key} style={{ display: 'grid', gridTemplateColumns: 'minmax(84px, 120px) 1fr 62px', alignItems: 'center', gap: 10, padding: '7px 0' }}>
              <div style={{ fontSize: '0.8rem', fontWeight: 700, color: r.final ? GOLD : 'var(--text-main, #1e293b)' }}>{r.label}</div>
              <div style={{ position: 'relative', height: 18, borderRadius: 9, background: '#f8fafc', overflow: 'hidden' }}>
                <div style={{ position: 'absolute', inset: 0, width: `${max ? Math.max(2.5, (n / max) * 100) : 0}%`, borderRadius: 9, background: r.final ? `linear-gradient(90deg, ${GOLD}, #fbbf24)` : `linear-gradient(90deg, #8b5cf6, ${ACCENT})` }} />
              </div>
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: '0.82rem', fontWeight: 800 }}>{pctOfTotal.toFixed(1)}%</div>
                <div style={{ fontSize: '0.64rem', color: 'var(--text-muted)' }}>{n} cases</div>
              </div>
            </div>
          );
        })}
      </div>
      <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: 12, marginBottom: 0 }}>
        A live distribution of the pipeline; later stages fill in as applicants report progress. Bars are scaled to the largest group.
      </p>
    </div>
  );
};

export const ProcessingTimesPage: FC<{ onNavigate?: (p: string) => void }> = () => {
  return (
    <>
      <SEO
        title="Express Entry Processing Times 2026: AOR to PPR, Biometrics & eCOPR | Mentor Visa"
        description={`How long does Express Entry take in 2026? Median timelines from ${TOTAL} real cases — AOR to biometrics, medical, final decision, P1/P2, PPR and eCOPR, by stream (CEC, FSW, PNP). See when to raise a webform.`}
        keywords="Express Entry processing time 2026, AOR to PPR time, how long after biometrics PPR, eCOPR timeline, P1 P2 portal timeline, CEC processing time, Express Entry timeline, IRCC processing time"
        canonical="/express-entry-processing-times"
        schema={FAQ_SCHEMA}
      />

      <section className="page-hero">
        <div className="page-hero-content">
          <div className="page-hero-badge">📊 Based on {TOTAL}+ real recent cases</div>
          <h1>Express Entry Processing Times<br /><span className="hero-highlight" style={{ color: 'var(--primary-light)' }}>AOR to eCOPR, in 2026.</span></h1>
          <p style={{ maxWidth: '720px', margin: '0 auto 24px auto', fontSize: '1.1rem', lineHeight: '1.6' }}>
            How long does each Express Entry milestone actually take? Below are median timelines from {TOTAL} real,
            recent permanent-residence applications — by stream and milestone — so you know what's normal and when your
            case is running late.
          </p>
          <a href="/track-my-application" className="btn btn-primary btn-lg" style={{ textDecoration: 'none', display: 'inline-block' }}>
            Get predictions for my case →
          </a>
        </div>
      </section>

      <div className="page-container">
        {/* Headline stats */}
        <section className="page-section">
          <div className="pt-stats">
            <div className="pt-stat">
              <div className="pt-stat-number">{ALL.aor_to_ppr?.median}<span className="pt-stat-unit">d</span></div>
              <div className="pt-stat-label">Median AOR → PPR / Portal 2</div>
            </div>
            <div className="pt-stat">
              <div className="pt-stat-number">{ALL.aor_to_ecopr?.median}<span className="pt-stat-unit">d</span></div>
              <div className="pt-stat-label">Median AOR → eCOPR</div>
            </div>
            <div className="pt-stat">
              <div className="pt-stat-number">{ALL.aor_to_bil?.median}<span className="pt-stat-unit">d</span></div>
              <div className="pt-stat-label">Median AOR → Biometrics</div>
            </div>
            <div className="pt-stat">
              <div className="pt-stat-number">{TOTAL}<span className="pt-stat-unit">+</span></div>
              <div className="pt-stat-label">Real cases in the dataset</div>
            </div>
          </div>
        </section>

        {/* What changed since the last monthly update */}
        <section className="page-section">
          <WhatChanged />
        </section>

        {/* The Typical Journey — dot-and-bar chart with per-stream tabs */}
        <section className="page-section">
          <TypicalJourney />
        </section>

        <section className="page-section">
          <h2 style={{ fontSize: '1.5rem', fontWeight: 800, marginBottom: 6 }}>Median days between milestones, by stream</h2>
          <p style={{ color: 'var(--text-muted)', marginBottom: 18 }}>
            Median (50th-percentile) days from AOR. Lower is faster. Based on {TOTAL} cases reported since mid-2025.
          </p>
          <div className="pt-table-wrap">
            <table className="pt-table">
              <thead>
                <tr>
                  <th>Milestone</th>
                  {STREAMS.map(s => (<th key={s}>{s}</th>))}
                  <th className="pt-col-all">All</th>
                </tr>
              </thead>
              <tbody>
                {ROWS.map(r => (
                  <tr key={r.key}>
                    <td>{r.label}</td>
                    {STREAMS.map(s => (
                      <td key={s} className={BY_STREAM[s]?.[r.key] ? '' : 'pt-cell-empty'}>
                        {fmtRange(BY_STREAM[s]?.[r.key])}
                      </td>
                    ))}
                    <td className="pt-col-all">{fmtRange(ALL[r.key])}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: 10 }}>
            Community-reported data, updated monthly (last updated {data.generated}). Estimates only — not a guarantee or legal advice.
          </p>
        </section>

        {/* Community snapshot — where tracked applicants are right now */}
        <section className="page-section">
          <CommunitySnapshot />
        </section>

        <section className="page-section">
          <h2 style={{ fontSize: '1.5rem', fontWeight: 800, marginBottom: 12 }}>How the Express Entry timeline works</h2>
          <p style={{ lineHeight: 1.7, marginBottom: 12 }}>
            After you submit your e-APR and receive your <strong>AOR</strong>, IRCC works through a predictable sequence:
            a <strong>biometrics</strong> request (median {ALL.aor_to_bil?.median} days after AOR), the <strong>medical</strong>
            passing (~{ALL.aor_to_meds?.median} days), <strong>background checks</strong>, and a <strong>final decision</strong>
            (~{ALL.aor_to_decision?.median} days). Outland applicants then receive a <strong>PPR</strong> (passport request),
            while inland applicants move through the PR Portal — <strong>P1</strong> (~{ALL.aor_to_p1?.median} days) and
            <strong> P2</strong> — before the final <strong>eCOPR</strong> (~{ALL.aor_to_ecopr?.median} days from AOR).
          </p>
          <p style={{ lineHeight: 1.7 }}>
            Timelines vary a lot by stream — outland FSW biometrics are typically requested within a few weeks, while CEC
            biometrics cluster around {cec('aor_to_bil')?.median} days. That's why a single "average" is misleading, and why
            our tracker predicts your dates from applicants who match your stream, country and category.
          </p>
        </section>

        <section className="page-section">
          <h2 style={{ fontSize: '1.5rem', fontWeight: 800, marginBottom: 16 }}>Frequently asked questions</h2>
          {FAQ.map((f, i) => (
            <div key={i} className="pt-faq-item">
              <h3>{f.q}</h3>
              <p>{f.a}</p>
            </div>
          ))}
        </section>

        <section className="page-section" style={{ textAlign: 'center' }}>
          <h2 style={{ fontSize: '1.4rem', fontWeight: 800, marginBottom: 10 }}>Want predictions for <em>your</em> case?</h2>
          <p style={{ color: 'var(--text-muted)', maxWidth: 600, margin: '0 auto 20px' }}>
            Track every milestone and get expected dates tailored to your stream, country and category — plus a flag when your case is running slower than typical.
          </p>
          <a href="/track-my-application" className="btn btn-primary btn-lg" style={{ textDecoration: 'none', display: 'inline-block' }}>
            Open the Application Tracker →
          </a>
        </section>
      </div>
    </>
  );
};

export default ProcessingTimesPage;
