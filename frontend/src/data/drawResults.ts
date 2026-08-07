// Express Entry Draw Results — 2025 & 2026
// Source: IRCC official draw results
// This data powers both the Draw Results page and the ITA Strategy scoring

import rawDraws from './draw_results.json';

export interface DrawResult {
  date: string;
  drawType: 'General' | 'CEC' | 'PNP' | 'French' | 'Healthcare' | 'STEM' | 'Trades' | 'Transport' | 'Agriculture' | 'Senior Managers' | 'Physicians' | 'Education' | 'Military';
  crsScore: number;
  itasIssued: number;
  notes?: string;
}

export const ALL_DRAWS: DrawResult[] = rawDraws as DrawResult[];

// Helper: Get the latest CRS cutoff for a specific draw type
export function getLatestCutoff(drawType: string): number | null {
  const draw = ALL_DRAWS.find(d => d.drawType === drawType);
  return draw ? draw.crsScore : null;
}

// Helper: Get average CRS for a specific draw type in the last N months
export function getAverageCutoff(drawType: string, months: number = 6): number {
  const cutoffDate = new Date();
  cutoffDate.setMonth(cutoffDate.getMonth() - months);
  const draws = ALL_DRAWS.filter(d => d.drawType === drawType && new Date(d.date) >= cutoffDate);
  if (draws.length === 0) {
    // fallback if no draws in last 6 months (like trades/physicians)
    const allOfThisType = ALL_DRAWS.filter(d => d.drawType === drawType);
    if (!allOfThisType.length) return 0;
    return Math.round(allOfThisType.slice(0, Math.min(3, allOfThisType.length)).reduce((sum, d) => sum + d.crsScore, 0) / Math.min(3, allOfThisType.length));
  }
  return Math.round(draws.reduce((sum, d) => sum + d.crsScore, 0) / draws.length);
}

// Helper: Get CRS trend (is it going up or down?)
export function getTrend(drawType: string): 'rising' | 'falling' | 'stable' {
  const draws = ALL_DRAWS.filter(d => d.drawType === drawType).slice(0, 6);
  if (draws.length < 3) return 'stable';
  const recentLength = Math.min(3, draws.length);
  const recentAvg = draws.slice(0, 3).reduce((s, d) => s + d.crsScore, 0) / recentLength;
  const olderCount = draws.slice(3, 6).length;
  if (olderCount === 0) return 'stable';
  const olderAvg = draws.slice(3, 6).reduce((s, d) => s + d.crsScore, 0) / olderCount;
  const diff = recentAvg - olderAvg;
  if (diff > 5) return 'rising';
  if (diff < -5) return 'falling';
  return 'stable';
}

// URL slug for a draw's detail page, e.g. "2026-07-10-senior-managers".
// Keep in sync with scripts/prerender.mjs (same formula).
export function drawSlug(d: DrawResult): string {
  return `${d.date}-${d.drawType.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
}

export function findDrawBySlug(slug: string): DrawResult | undefined {
  return ALL_DRAWS.find(d => drawSlug(d) === slug);
}

// Draw type color mapping
export const DRAW_TYPE_COLORS: Record<string, string> = {
  'CEC': '#3b82f6',
  'PNP': '#6366f1',
  'French': '#10b981',
  'Healthcare': '#ef4444',
  'Trades': '#f59e0b',
  'Education': '#8b5cf6',
  'General': '#64748b',
  'Physicians': '#ec4899',
  'Senior Managers': '#14b8a6',
  'Military': '#0ea5e9',
  'Transport': '#d97706',
};
