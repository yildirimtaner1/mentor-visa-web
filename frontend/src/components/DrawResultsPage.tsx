import { type FC, useState, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { SEO } from './common/SEO';
import { ALL_DRAWS, DRAW_TYPE_COLORS, getAverageCutoff, getTrend, drawSlug } from '../data/drawResults';

const DRAW_TYPE_LABELS: Record<string, string> = {
  'CEC': 'Canadian Experience Class',
  'PNP': 'Provincial Nominee Program',
  'French': 'French-Language Proficiency',
  'Healthcare': 'Healthcare & Social Services',
  'Trades': 'Trades',
  'Education': 'Education',
  'General': 'No Program Specified',
  'Physicians': 'Physicians',
  'Senior Managers': 'Senior Managers',
  'Military': 'Skilled Military Recruits',
  'Transport': 'Transport Occupations',
};

const FILTER_OPTIONS = ['All', 'CEC', 'PNP', 'French', 'Healthcare', 'Trades', 'Other'];

export const DrawResultsPage: FC<{ onNavigate: (v: string) => void }> = () => {
  const [filter, setFilter] = useState('CEC');
  const [year, setYear] = useState('2026');

  const filtered = useMemo(() => {
    return ALL_DRAWS.filter(d => {
      const yearMatch = d.date.startsWith(year);
      if (filter === 'All') return yearMatch;
      if (filter === 'Other') return yearMatch && !['CEC', 'PNP', 'French', 'Healthcare', 'Trades'].includes(d.drawType);
      return yearMatch && d.drawType === filter;
    });
  }, [filter, year]);

  // Dynamic category for Stats and Chart (fallback to CEC if "All" or "Other" is selected)
  const activeCategory = (filter === 'All' || filter === 'Other') ? 'CEC' : filter;
  const categoryLabel = DRAW_TYPE_LABELS[activeCategory] || activeCategory;
  const categoryColor = DRAW_TYPE_COLORS[activeCategory] || 'var(--primary-color)';

  // Stats
  const totalITAs = useMemo(() => filtered.reduce((s, d) => s + d.itasIssued, 0), [filtered]);
  const avgScore = useMemo(() => getAverageCutoff(activeCategory, 6), [activeCategory]);
  const trend = useMemo(() => getTrend(activeCategory), [activeCategory]);
  const latestDraw = useMemo(() => ALL_DRAWS.find(d => d.drawType === activeCategory), [activeCategory]);
  
  const [lowestScore, highestScore] = useMemo(() => {
    const draws = ALL_DRAWS.filter(d => d.drawType === activeCategory && d.date.startsWith(year));
    if (!draws.length) return [0, 0];
    const scores = draws.map(d => d.crsScore);
    return [Math.min(...scores), Math.max(...scores)];
  }, [activeCategory, year]);

  // CRS chart data — active category draws only, last 12
  const chartData = useMemo(() => {
    return ALL_DRAWS
      .filter(d => d.drawType === activeCategory)
      .slice(0, 12)
      .reverse();
  }, [activeCategory]);

  const chartMax = useMemo(() => Math.max(...chartData.map(d => d.crsScore)) + 10, [chartData]);
  const chartMin = useMemo(() => Math.min(...chartData.map(d => d.crsScore)) - 10, [chartData]);

  const formatDate = (date: string) => {
    const d = new Date(date + 'T00:00:00');
    return d.toLocaleDateString('en-CA', { month: 'short', day: 'numeric', year: 'numeric' });
  };

  const schemaData = JSON.stringify({
    "@context": "https://schema.org",
    "@type": "Dataset",
    "name": "Express Entry Draw Results 2025-2026",
    "description": "Complete history of Canadian Express Entry draw results including CRS cutoff scores, ITAs issued, and draw types.",
    "url": "https://mentorvisa.com/draw-results",
    "temporalCoverage": "2025/2026",
    "creator": { "@type": "Organization", "name": "Mentor Visa" }
  });

  return (
    <div>
      <SEO
        title="Express Entry Draw Results & CRS Scores 2026 | Mentor Visa"
        description="Track all Express Entry draw results for 2025-2026. View CRS cutoff scores, ITAs issued, CEC/PNP/category-based draws, and CRS score trends."
        canonical="/draw-results"
        keywords="Express Entry draws, CRS cutoff score, draw results 2026, CEC draws, PNP draws, ITA issued, Express Entry predictions"
        schema={schemaData}
      />

      <section className="page-hero">
        <div className="page-hero-content">
          <div className="page-hero-badge">📊 Live Draw Tracker</div>
          <h1>Express Entry<br /><span className="hero-highlight">Draw Results</span></h1>
          <p>Track CRS cutoff scores, ITAs issued, and trends across all Express Entry draw types. Updated after every IRCC round.</p>
        </div>
      </section>

      <div className="page-container">
        <div style={{ maxWidth: '960px', margin: '0 auto' }}>

          {/* Stats Cards */}
          <section className="page-section" style={{ paddingBottom: '0' }}>
            <div className="draw-stat-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '20px' }}>
              
              <div className="draw-stat-card" style={{ padding: '24px', background: 'white', borderRadius: '16px', boxShadow: '0 4px 20px rgba(0,0,0,0.03)', border: '1px solid rgba(0,0,0,0.05)', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                <div style={{ width: '40px', height: '4px', background: categoryColor, borderRadius: '4px', marginBottom: '16px' }} />
                <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '8px', textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 600 }}>Latest {activeCategory} Cutoff</div>
                <div style={{ fontSize: '2.5rem', fontWeight: 800, color: '#1E293B', lineHeight: 1 }}>{latestDraw?.crsScore || '—'}</div>
                <div style={{ fontSize: '0.85rem', color: 'var(--primary-color)', marginTop: '8px', fontWeight: 500 }}>{latestDraw ? formatDate(latestDraw.date) : ''}</div>
              </div>

              <div className="draw-stat-card" style={{ padding: '24px', background: 'white', borderRadius: '16px', boxShadow: '0 4px 20px rgba(0,0,0,0.03)', border: '1px solid rgba(0,0,0,0.05)', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                <div style={{ width: '40px', height: '4px', background: categoryColor, borderRadius: '4px', marginBottom: '16px' }} />
                <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '8px', textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 600 }}>6-Month {activeCategory} Avg</div>
                <div style={{ fontSize: '2.5rem', fontWeight: 800, color: '#1E293B', lineHeight: 1 }}>{avgScore}</div>
                <div style={{ fontSize: '0.85rem', marginTop: '8px', fontWeight: 500, color: trend === 'falling' ? '#10b981' : trend === 'rising' ? '#ef4444' : 'var(--text-muted)' }}>
                  {trend === 'falling' ? '↓ Trending down' : trend === 'rising' ? '↑ Trending up' : '→ Stable trend'}
                </div>
              </div>

              <div className="draw-stat-card" style={{ padding: '24px', background: 'white', borderRadius: '16px', boxShadow: '0 4px 20px rgba(0,0,0,0.03)', border: '1px solid rgba(0,0,0,0.05)', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                <div style={{ width: '40px', height: '4px', background: categoryColor, borderRadius: '4px', marginBottom: '16px' }} />
                <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '8px', textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 600 }}>{activeCategory} Range ({year})</div>
                <div style={{ fontSize: '2.5rem', fontWeight: 800, color: '#1E293B', lineHeight: 1 }}>{lowestScore}–{highestScore}</div>
                <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginTop: '8px', fontWeight: 500 }}>Lowest – Highest</div>
              </div>

              <div className="draw-stat-card draw-stat-card--dark" style={{ padding: '24px', background: 'linear-gradient(135deg, #1E293B 0%, #0F172A 100%)', borderRadius: '16px', boxShadow: '0 4px 20px rgba(0,0,0,0.08)', display: 'flex', flexDirection: 'column', alignItems: 'center', color: 'white' }}>
                <div style={{ width: '40px', height: '4px', background: 'rgba(255,255,255,0.2)', borderRadius: '4px', marginBottom: '16px' }} />
                <div style={{ fontSize: '0.8rem', color: '#94A3B8', marginBottom: '8px', textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 600 }}>Total ITAs (Filter: {filter})</div>
                <div style={{ fontSize: '2.5rem', fontWeight: 800, lineHeight: 1, color: '#38BDF8' }}>{totalITAs.toLocaleString()}</div>
                <div style={{ fontSize: '0.85rem', color: '#94A3B8', marginTop: '8px', fontWeight: 500 }}>{filtered.length} draws in {year}</div>
              </div>

            </div>
          </section>

          {/* CRS Trend Chart */}
          <section className="page-section" style={{ paddingBottom: '0' }}>
            <h2 style={{ marginBottom: '20px', display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ display: 'inline-block', width: '12px', height: '12px', borderRadius: '50%', background: categoryColor }}></span>
              {categoryLabel} Score Trend
            </h2>
            <div style={{
              background: 'white', borderRadius: '16px', boxShadow: '0 4px 20px rgba(0,0,0,0.03)', border: '1px solid rgba(0,0,0,0.05)',
              padding: '32px 24px'
            }} className="draw-chart-card">
              <div className="draw-chart-track" style={{ display: 'flex', alignItems: 'flex-end', gap: '6px', height: '220px', position: 'relative' }}>
                {chartData.map((d, i) => {
                  const range = chartMax - chartMin;
                  const height = ((d.crsScore - chartMin) / range) * 180 + 30; // Min 30px height
                  return (
                    <div key={i} className="draw-chart-col" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px' }}>
                      <span style={{ fontSize: '0.75rem', fontWeight: 800, color: categoryColor }}>{d.crsScore}</span>
                      <div style={{
                        width: '100%', maxWidth: '44px', height: `${height}px`,
                        background: `linear-gradient(180deg, ${categoryColor}, ${categoryColor}99)`,
                        borderRadius: '6px 6px 0 0', transition: 'height 0.4s cubic-bezier(0.4, 0, 0.2, 1)',
                        cursor: 'pointer', position: 'relative', boxShadow: '0 -2px 10px rgba(0,0,0,0.05)'
                      }}
                        title={`${formatDate(d.date)}: CRS ${d.crsScore}`}
                      />
                      <span style={{
                        fontSize: '0.65rem', color: 'var(--text-muted)', transform: 'rotate(-45deg)',
                        whiteSpace: 'nowrap', textAlign: 'center', fontWeight: 500, marginTop: '4px'
                      }}>
                        {new Date(d.date + 'T00:00:00').toLocaleDateString('en-CA', { month: 'short', day: 'numeric' })}
                      </span>
                    </div>
                  );
                })}
              </div>
              <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '24px', textAlign: 'center', fontWeight: 500 }}>
                Last {chartData.length} {categoryLabel} draws
                <span className="draw-chart-hint"> · swipe to see all →</span>
              </p>
            </div>
          </section>

          {/* Filters */}
          <section className="page-section" style={{ paddingBottom: '0' }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                {FILTER_OPTIONS.map(f => (
                  <button
                    key={f}
                    onClick={() => setFilter(f)}
                    style={{
                      padding: '6px 14px', borderRadius: '8px', fontSize: '0.82rem', fontWeight: 600,
                      border: filter === f ? '2px solid var(--primary-color)' : '1px solid var(--border-color)',
                      background: filter === f ? 'var(--primary-color)' : 'white',
                      color: filter === f ? 'white' : 'var(--text-color)',
                      cursor: 'pointer', transition: 'all 0.15s'
                    }}
                  >
                    {f === 'All' ? '🌐 All' : f}
                    {f !== 'All' && DRAW_TYPE_COLORS[f] && (
                      <span style={{
                        display: 'inline-block', width: '8px', height: '8px', borderRadius: '50%',
                        background: DRAW_TYPE_COLORS[f], marginLeft: '6px', verticalAlign: 'middle'
                      }} />
                    )}
                  </button>
                ))}
              </div>
              <div style={{ display: 'flex', gap: '8px' }}>
                {['2026', '2025'].map(y => (
                  <button
                    key={y}
                    onClick={() => setYear(y)}
                    style={{
                      padding: '6px 14px', borderRadius: '8px', fontSize: '0.82rem', fontWeight: 600,
                      border: year === y ? '2px solid var(--primary-color)' : '1px solid var(--border-color)',
                      background: year === y ? 'var(--primary-color)' : 'white',
                      color: year === y ? 'white' : 'var(--text-color)',
                      cursor: 'pointer'
                    }}
                  >
                    {y}
                  </button>
                ))}
              </div>
            </div>
          </section>

          {/* Results Table */}
          <section className="page-section" style={{ paddingBottom: '0' }}>
            <div style={{
              background: 'white', borderRadius: '12px', border: '1px solid var(--border-color)',
              overflowX: 'auto', overflowY: 'hidden', WebkitOverflowScrolling: 'touch'
            }}>
              <table className="draw-table" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9rem' }}>
                <thead>
                  <tr style={{ background: '#F8FAFC', borderBottom: '2px solid var(--border-color)' }}>
                    <th style={{ padding: '12px 16px', textAlign: 'left', fontWeight: 600, color: 'var(--text-muted)', fontSize: '0.78rem', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Date</th>
                    <th style={{ padding: '12px 16px', textAlign: 'left', fontWeight: 600, color: 'var(--text-muted)', fontSize: '0.78rem', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Draw Type</th>
                    <th style={{ padding: '12px 16px', textAlign: 'center', fontWeight: 600, color: 'var(--text-muted)', fontSize: '0.78rem', textTransform: 'uppercase', letterSpacing: '0.5px' }}>CRS Score</th>
                    <th style={{ padding: '12px 16px', textAlign: 'right', fontWeight: 600, color: 'var(--text-muted)', fontSize: '0.78rem', textTransform: 'uppercase', letterSpacing: '0.5px' }}>ITAs Issued</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((d, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid var(--border-color)', transition: 'background 0.1s' }}
                      onMouseOver={e => { e.currentTarget.style.background = '#F8FAFC'; }}
                      onMouseOut={e => { e.currentTarget.style.background = 'white'; }}
                    >
                      <td style={{ padding: '12px 16px', fontWeight: 500, whiteSpace: 'nowrap' }}>
                        <a href={`/draw-results/${drawSlug(d)}`} style={{ color: 'inherit', textDecoration: 'none' }}>{formatDate(d.date)}</a>
                      </td>
                      <td style={{ padding: '12px 16px' }}>
                        <a href={`/draw-results/${drawSlug(d)}`} style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'inherit', textDecoration: 'none' }}>
                          <span style={{
                            width: '10px', height: '10px', borderRadius: '50%', flexShrink: 0,
                            background: DRAW_TYPE_COLORS[d.drawType] || '#64748b'
                          }} />
                          <span style={{ textDecoration: 'underline', textDecorationColor: '#CBD5E1', textUnderlineOffset: '3px' }}>{DRAW_TYPE_LABELS[d.drawType] || d.drawType}</span>
                        </a>
                      </td>
                      <td style={{ padding: '12px 16px', textAlign: 'center' }}>
                        <span style={{
                          fontWeight: 700, fontSize: '0.95rem',
                          color: d.crsScore > 600 ? '#6366f1' : d.crsScore > 500 ? '#3b82f6' : d.crsScore > 450 ? '#10b981' : '#16a34a',
                          background: d.crsScore > 600 ? '#6366f114' : d.crsScore > 500 ? '#3b82f614' : d.crsScore > 450 ? '#10b98114' : '#16a34a14',
                          padding: '3px 10px', borderRadius: '6px'
                        }}>
                          {d.crsScore}
                        </span>
                      </td>
                      <td style={{ padding: '12px 16px', textAlign: 'right', fontWeight: 600 }}>{d.itasIssued.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {filtered.length === 0 && (
                <div style={{ padding: '32px', textAlign: 'center', color: 'var(--text-muted)' }}>
                  No draws found for this filter/year combination.
                </div>
              )}
            </div>
          </section>

          {/* CTA */}
          <section className="page-section">
            <div style={{ padding: '32px', background: 'linear-gradient(135deg, #EFF6FF, #F8FAFC)', borderRadius: '16px', border: '1px solid var(--border-color)', textAlign: 'center' }}>
              <h3 style={{ marginBottom: '12px' }}>Where does your CRS score stand?</h3>
              <p style={{ color: 'var(--text-muted)', fontSize: '0.95rem', marginBottom: '20px' }}>
                Calculate your exact CRS score and see how you compare against recent draw results.
              </p>
              <div style={{ display: 'flex', gap: '12px', justifyContent: 'center', flexWrap: 'wrap' }}>
                <Link to="/crs-calculator" className="btn btn-primary">🧮 Calculate My CRS Score</Link>
                <Link to="/find-my-noc" className="btn btn-outline">🎯 Find My NOC Code</Link>
              </div>
            </div>
          </section>

        </div>
      </div>
    </div>
  );
};
