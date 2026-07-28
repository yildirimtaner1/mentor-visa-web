/**
 * ScoreVsCutoff — The "Anxiety Hook" Widget
 * 
 * Shows the user's CRS score against the latest draw cutoffs in a visually
 * compelling way that creates urgency. This is the conversion trigger.
 * 
 * Features:
 * - Score vs Latest Cutoff gauge
 * - "You'd be invited in X of last Y draws" stat
 * - Category-based draw matches (if NOC is known)
 * - Contextual CTA based on gap size
 */

import { useMemo } from 'react';
import { ALL_DRAWS, getTrend } from '../data/drawResults';
import { getCategoriesForNoc, CATEGORY_INFO } from '../data/nocCategoryMap';
import { checkDrawEligibility, type SimulatorProfile } from '../utils/crsSimulator';
import { useJourneyStore } from '../stores/journeyStore';
import './ScoreVsCutoff.css';

const DRAW_TYPE_COLORS: Record<string, string> = {
  'General': '#64748b',
  'CEC': '#3b82f6',
  'French': '#8b5cf6',
  'STEM': '#10b981',
  'Healthcare': '#059669',
  'Trades': '#f59e0b',
  'Transport': '#d97706',
  'Agriculture': '#16a34a',
  'PNP': '#0f172a',
  'Military': '#0ea5e9'
};

interface ScoreVsCutoffProps {
  userScore: number;
  onOpenWarRoom?: () => void;
}

export function ScoreVsCutoff({ userScore, onOpenWarRoom }: ScoreVsCutoffProps) {
  const { noc, profile } = useJourneyStore();

  const simulatorProfile = useMemo<SimulatorProfile>(() => {
    const clbMin = Math.min(
      profile.primaryLanguage?.speaking ?? 0,
      profile.primaryLanguage?.listening ?? 0,
      profile.primaryLanguage?.reading ?? 0,
      profile.primaryLanguage?.writing ?? 0
    );
    const secondClbMin = profile.secondaryLanguage ? Math.min(
      profile.secondaryLanguage.speaking ?? 0,
      profile.secondaryLanguage.listening ?? 0,
      profile.secondaryLanguage.reading ?? 0,
      profile.secondaryLanguage.writing ?? 0
    ) : 0;
    
    const FRENCH_TESTS = ['tef', 'tcf'];
    let hasFrenchSkills = false;
    if (FRENCH_TESTS.includes(profile.primaryLanguage?.test || '') && clbMin >= 7) hasFrenchSkills = true;
    if (FRENCH_TESTS.includes(profile.secondaryLanguage?.test || '') && secondClbMin >= 7) hasFrenchSkills = true;

    return {
      crsScore: userScore,
      age: profile.age,
      hasSpouse: profile.maritalStatus === 'married' || profile.maritalStatus === 'common_law',
      spouseAccompanying: profile.spouseAccompanying ?? false,
      minClb: clbMin,
      hasSecondLanguage: profile.secondaryLanguage !== null,
      secondLanguageMinClb: secondClbMin,
      educationLevel: profile.educationLevel,
      canadianExperienceYears: profile.canadianExperienceYears ?? 0,
      foreignExperienceYears: (profile.totalSkilledExperienceYears ?? 0) - (profile.canadianExperienceYears ?? 0),
      hasProvincialNomination: profile.hasProvincialNomination ?? false,
      hasCanadianEducation: profile.educationInCanada ?? false,
      hasFrenchSkills,
      hasSiblingInCanada: profile.hasRelativeInCanada ?? false,
      spouseClbMin: profile.spouseLanguage ? Math.min(
        profile.spouseLanguage.speaking || 0,
        profile.spouseLanguage.listening || 0,
        profile.spouseLanguage.reading || 0,
        profile.spouseLanguage.writing || 0
      ) : 0,
      spouseEducation: profile.spouseEducationLevel,
      spouseCanadianYears: profile.spouseCanadianExperienceYears ?? 0,
    };
  }, [userScore, profile]);

  // Latest general draw
  const latestGeneral = useMemo(() => {
    const draw = ALL_DRAWS.find(d => d.drawType === 'General' || d.drawType === 'CEC');
    return draw || null;
  }, []);

  const latestCutoff = latestGeneral?.crsScore ?? 0;
  const gap = userScore - latestCutoff;
  const isAbove = gap >= 0;

  // Trend for general draws
  const trend = useMemo(() => getTrend('CEC'), []);

  // How many draws the user would have been invited in (last 12 months)
  const invitationStats = useMemo(() => {
    const cutoffDate = new Date();
    cutoffDate.setMonth(cutoffDate.getMonth() - 12);
    const recentDraws = ALL_DRAWS.filter(
      d => (d.drawType === 'General' || d.drawType === 'CEC') && new Date(d.date) >= cutoffDate
    );
    const invited = recentDraws.filter(d => userScore >= d.crsScore).length;
    return { invited, total: recentDraws.length };
  }, [userScore]);

  // Category-based draw matches
  const categoryMatches = useMemo(() => {
    if (!noc.code) return [];
    const categories = getCategoriesForNoc(noc.code);
    return categories
      .map(cat => ({
        category: cat,
        info: CATEGORY_INFO[cat],
        userAbove: userScore >= CATEGORY_INFO[cat].avgCutoff2025,
        diff: userScore - CATEGORY_INFO[cat].avgCutoff2025,
      }))
      .slice(0, 3);
  }, [noc.code, userScore]);

  // All draws (any type) the user would qualify for
  const allDrawInvitations = useMemo(() => {
    const cutoffDate = new Date();
    cutoffDate.setMonth(cutoffDate.getMonth() - 6);
    const recent = ALL_DRAWS.filter(d => new Date(d.date) >= cutoffDate);
    const qualified = recent.filter(d => {
      const result = checkDrawEligibility(d.drawType, simulatorProfile, noc.code);
      return result.status === 'eligible' && userScore >= d.crsScore;
    });
    return { qualified: qualified.length, total: recent.length, draws: qualified.slice(0, 5) };
  }, [userScore, simulatorProfile, noc.code]);

  // Determine status
  const status: 'excellent' | 'competitive' | 'below' | 'far_below' = 
    gap >= 30 ? 'excellent' :
    gap >= 0 ? 'competitive' :
    gap >= -30 ? 'below' :
    'far_below';

  const statusConfig = {
    excellent: { emoji: '🎉', color: '#10b981', label: 'Excellent', msg: 'You\'re well above the latest cutoff. An ITA is highly likely in the next draw.' },
    competitive: { emoji: '⚡', color: '#10b981', label: 'Competitive', msg: 'You\'re at or above the cutoff. Stay ready — your ITA could come any draw.' },
    below: { emoji: '⚠️', color: '#f59e0b', label: 'Close', msg: `You're ${Math.abs(gap)} points below the latest cutoff. A few improvements could get you there.` },
    far_below: { emoji: '🔴', color: '#ef4444', label: 'Gap to Close', msg: `You need ${Math.abs(gap)} more points. But category-based draws may have lower cutoffs — check below.` },
  };

  const config = statusConfig[status];

  // Gauge calculation (0-100% where 50% = at cutoff)
  const gaugePercent = Math.min(100, Math.max(5, ((userScore / (latestCutoff * 1.3)) * 100)));

  return (
    <div className="svc-widget">
      <div className="svc-header">
        <span className="svc-header-icon">{config.emoji}</span>
        <div>
          <h3 className="svc-header-title">Your Score vs Express Entry Cutoff</h3>
          <p className="svc-header-subtitle">Based on the most recent draws</p>
        </div>
      </div>

      {/* Main Score Comparison */}
      <div className="svc-comparison">
        <div className="svc-score-col">
          <div className="svc-score-label">Your Score</div>
          <div className="svc-score-value" style={{ color: config.color }}>{userScore}</div>
        </div>

        <div className="svc-vs">
          <div className={`svc-gap-badge ${isAbove ? 'positive' : 'negative'}`}>
            {isAbove ? '+' : ''}{gap}
          </div>
          <span className="svc-vs-text">vs</span>
        </div>

        <div className="svc-score-col">
          <div className="svc-score-label">Latest Cutoff</div>
          <div className="svc-score-value svc-cutoff">{latestCutoff}</div>
          <div className="svc-draw-date">
            {latestGeneral?.drawType} — {latestGeneral?.date ? new Date(latestGeneral.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : ''}
          </div>
        </div>
      </div>

      {/* Progress Gauge */}
      <div className="svc-gauge">
        <div className="svc-gauge-track">
          <div 
            className="svc-gauge-fill"
            style={{ 
              width: `${gaugePercent}%`,
              background: isAbove 
                ? 'linear-gradient(90deg, #10b981, #059669)'
                : gap >= -20 
                  ? 'linear-gradient(90deg, #f59e0b, #d97706)'
                  : 'linear-gradient(90deg, #ef4444, #dc2626)',
            }}
          />
          <div 
            className="svc-gauge-cutoff-marker"
            style={{ left: `${Math.min(95, (latestCutoff / (latestCutoff * 1.3)) * 100)}%` }}
          >
            <div className="svc-gauge-cutoff-label">Cutoff</div>
          </div>
        </div>
      </div>

      {/* Context Message */}
      <div className={`svc-context ${status}`}>
        <p>{config.msg}</p>
        {trend === 'rising' && !isAbove && (
          <p className="svc-trend-warning">📈 Cutoff trend is <strong>rising</strong> — act soon before it climbs higher.</p>
        )}
        {trend === 'falling' && !isAbove && (
          <p className="svc-trend-good">📉 Cutoff trend is <strong>falling</strong> — you may qualify in upcoming draws.</p>
        )}
      </div>

      {/* Stats Row */}
      <div className="svc-stats">
        <div className="svc-stat-item">
          <div className="svc-stat-value" style={{ 
            color: invitationStats.invited > invitationStats.total / 2 ? '#10b981' : '#ef4444' 
          }}>
            {invitationStats.invited}/{invitationStats.total}
          </div>
          <div className="svc-stat-label">General/CEC draws you'd qualify for (12 mo)</div>
        </div>
        <div className="svc-stat-item">
          <div className="svc-stat-value" style={{ color: '#6366f1' }}>
            {allDrawInvitations.qualified}/{allDrawInvitations.total}
          </div>
          <div className="svc-stat-label">All draws including category-based (6 mo)</div>
        </div>
      </div>

      {/* Category-Based Draw Matches */}
      {categoryMatches.length > 0 && (
        <div className="svc-categories">
          <h4 className="svc-categories-title">
            🏷️ Category-Based Draws for NOC {noc.code}
          </h4>
          <div className="svc-category-cards">
            {categoryMatches.map(match => (
              <div 
                key={match.category} 
                className={`svc-category-card ${match.userAbove ? 'above' : 'below'}`}
              >
                <div className="svc-category-icon">{match.info.icon}</div>
                <div className="svc-category-info">
                  <div className="svc-category-name">{match.info.name}</div>
                  <div className="svc-category-cutoff">Avg cutoff: {match.info.avgCutoff2025}</div>
                </div>
                <div className={`svc-category-diff ${match.userAbove ? 'positive' : 'negative'}`}>
                  {match.userAbove ? '+' : ''}{match.diff}
                </div>
              </div>
            ))}
          </div>
          {categoryMatches.some(m => m.userAbove) && !isAbove && (
            <p className="svc-category-hint">
              ✅ Even though you're below the general cutoff, you qualify for category-based draws with lower cutoffs!
            </p>
          )}
        </div>
      )}

      {/* Qualified Recent Draws */}
      {allDrawInvitations.qualified > 0 && allDrawInvitations.draws.length > 0 && (
        <div className="svc-recent-draws">
          <h4>Recent Draws You'd Qualify For</h4>
          <div className="svc-draws-list">
            {allDrawInvitations.draws.map((draw, i) => (
              <div key={i} className="svc-draw-chip">
                <span className="svc-draw-chip-type" style={{ color: DRAW_TYPE_COLORS[draw.drawType] || '#64748b' }}>
                  {draw.drawType}
                </span>
                <span className="svc-draw-chip-score">{draw.crsScore}</span>
                <span className="svc-draw-chip-date">
                  {new Date(draw.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* CTA */}
      {!isAbove && onOpenWarRoom && (
        <div className="svc-cta">
          <button className="svc-cta-btn" onClick={onOpenWarRoom}>
            🎯 See How to Close the {Math.abs(gap)}-Point Gap →
          </button>
          <p className="svc-cta-sub">Personalized improvement scenarios ranked by effort and impact</p>
        </div>
      )}
    </div>
  );
}
