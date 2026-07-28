/**
 * Build-time prerendering (SSG-lite) for crawlers.
 *
 * After `vite build`, this script writes a static `dist/<route>/index.html` for every
 * indexable route: the 516 NOC detail pages (with REAL content — lead statement, duties,
 * TEER, eligibility — from public/noc-data.json) plus the static content routes (correct
 * per-page meta + a content stub). Vercel serves filesystem hits before the SPA rewrite,
 * so crawlers get fully-formed HTML on the first fetch — no JS rendering required — while
 * users get the same file and React takes over on load (createRoot replaces #root).
 *
 * Head tags are injected with data-prerender so react-helmet-async adopts and replaces
 * them at mount — this is what prevents the duplicate-meta problem in the rendered DOM.
 *
 * No user-agent branching anywhere: everyone gets the same HTML (this is SSG, not
 * dynamic rendering, so there is no cloaking concern).
 */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const DIST = join(ROOT, 'dist');
const SITE = 'https://mentorvisa.com';
const OG_IMAGE = `${SITE}/og-image.png`;

const esc = (s) =>
  String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

// ── Static content routes (titles/descriptions mirror each page's SEO component) ──
const ROUTE_META = {
  '/audit-employment-letter': {
    title: 'AI Employment Letter Auditor | Mentor Visa',
    description: 'Upload your employment letter and get an instant AI audit against IRCC NOC duty requirements. Catch missing duties before IRCC does.',
    h1: 'AI Employment Letter Auditor',
  },
  '/find-my-noc': {
    title: 'AI NOC Code Finder | Mentor Visa',
    description: 'Paste your job duties and our AI instantly matches them to the correct 2021 NOC code for Express Entry.',
    h1: 'Find My NOC Code',
  },
  '/crs-calculator': {
    title: 'Free CRS Score Calculator 2026 | Mentor Visa',
    description: 'Calculate your Comprehensive Ranking System score for Express Entry. Get your exact CRS points breakdown in 2 minutes.',
    h1: 'CRS Score Calculator',
  },
  '/track-my-application': {
    title: 'Express Entry Application Tracker & Timeline Predictions | Mentor Visa',
    description: 'Track every Express Entry PR milestone and get processing-time predictions tailored to your stream, country and category from 700+ real recent applications.',
    h1: 'Smart Application Tracker',
  },
  '/express-entry-processing-times': {
    title: 'Express Entry Processing Times 2026 — AOR to eCOPR | Mentor Visa',
    description: 'How long does each Express Entry milestone actually take? Median timelines from 700+ real recent PR applications, by stream and milestone.',
    h1: 'Express Entry Processing Times',
  },
  '/draw-results': {
    title: 'Express Entry Draw Results & CRS Scores 2026 | Mentor Visa',
    description: 'Track all Express Entry draw results for 2025-2026. View CRS cutoff scores, ITAs issued, CEC/PNP/category-based draws, and CRS score trends.',
    h1: 'Express Entry Draw Results',
  },
  '/order-gcms-notes': {
    title: 'Order Your GCMS Notes — Full IRCC File in 30-40 Days | Mentor Visa',
    description: 'See exactly why your application is delayed or was refused. We file your ATIP request and email you the complete GCMS notes. $19.90 CAD flat.',
    h1: 'Order Your GCMS Notes',
  },
  '/how-to-read-gcms-notes': {
    title: 'How to Read Your GCMS Notes: Codes, Stages & Red Flags Explained | Mentor Visa',
    description: 'A plain-English guide to interpreting GCMS notes: the AOR-to-PPR pipeline, what R10, A11.2, RFV, ADR and PFL mean, and the red flags that predict delays or refusals.',
    h1: 'How to Read Your GCMS Notes',
  },
  '/gcms-notes-analyzer': {
    title: 'GCMS Notes AI Analyzer — Your IRCC File Explained in Plain English | Mentor Visa',
    description: 'Upload your GCMS or CBSA notes PDF and get a plain-English report: stage-by-stage status, officer remarks explained, red flags, and likely next steps. $19.90 CAD.',
    h1: 'GCMS Notes AI Analyzer',
  },
  '/express-entry-cec-guide': {
    title: 'Express Entry Canadian Experience Class (CEC) Document Checklist & Guide',
    description: 'Learn exactly what documents IRCC requires for the Canadian Experience Class (CEC) Express Entry program. Avoid application rejection with our official guidelines.',
    h1: 'Canadian Experience Class Guide',
  },
  '/cec-checklist': {
    title: 'Express Entry CEC Document Checklist 2026 | Application Tracker',
    description: 'Track all required documents for your Canadian Experience Class (CEC) immigration application. An interactive checklist for employment letters, language tests, and more.',
    h1: 'CEC Document Checklist',
  },
  '/get-started': {
    title: 'Am I Eligible for Canada PR? Free Express Entry Check | Mentor Visa',
    description: 'Free eligibility assessment for Express Entry (FSWP, CEC, FSTP). Find out if you qualify for Canada Permanent Residence in under 3 minutes.',
    h1: 'Check Your Express Entry Eligibility',
  },
  '/ai-profile-assistant': {
    title: 'Express Entry AI Assistant — Instant IRCC Application Help | Mentor Visa',
    description: 'Ask anything about your Express Entry profile or e-APR and get instant, accurate answers. AI help for IRCC portal questions, document fields, and tricky sections.',
    h1: 'Express Entry AI Assistant',
  },
  '/gckey-setup-guide': {
    title: 'GCKey Setup Guide — Create Your IRCC Account | Mentor Visa',
    description: 'Step-by-step guide to creating your GCKey and IRCC secure account for Express Entry. Screenshots, common errors, and fixes.',
    h1: 'GCKey Setup Guide',
  },
  '/documents': {
    title: '12 Mistakes That Get PR Applications Refused | Mentor Visa',
    description: "Don't let a preventable mistake cost you your Canada PR. Learn the 12 most common errors and how to avoid them.",
    h1: '12 Mistakes That Get PR Applications Refused',
  },
  '/glossary': {
    title: 'Canadian Immigration Glossary — 70+ Terms | Mentor Visa',
    description: 'Comprehensive glossary of 70+ Canadian immigration terms. Understand CRS, IRCC, NOC, LMIA, PNP, and more.',
    h1: 'Canadian Immigration Glossary',
  },
  '/noc-codes': {
    title: 'Complete NOC 2021 Code Directory — All 516 Occupations | Mentor Visa',
    description: 'Browse all 516 NOC 2021 occupation codes. Filter by TEER, search by job title, and check CEC eligibility.',
    h1: 'NOC 2021 Code Directory',
  },
  '/pricing': {
    title: 'Pricing — Mentor Visa Canada PR Platform',
    description: 'Choose the plan that fits your PR journey. Free tools, Optimize bundle ($49), or Execute package ($99) with AI auditors. One-time payments, no subscription.',
    h1: 'Pricing',
  },
  '/contact': {
    title: 'Contact Us — Mentor Visa',
    description: 'Questions about your Express Entry tools, a GCMS notes order, or billing? Send us a message — we reply within 1 business day. contact@mentorvisa.com',
    h1: 'Contact Us',
  },
};

const TEER_LABELS = {
  0: 'Management', 1: 'Professional (university degree)', 2: 'Technical / skilled trades (college or apprenticeship)',
  3: 'Intermediate (high school and/or job-specific training)', 4: 'Labour (on-the-job training)', 5: 'Labour (no formal education required)',
};

// ── Template handling ──────────────────────────────────────────────────────────
const template = readFileSync(join(DIST, 'index.html'), 'utf-8');

// Routes with a dedicated OG card (public/og/<route>.png, from backend/gen_og_images.py).
const OG_ROUTES = new Set([
  '/crs-calculator', '/find-my-noc', '/audit-employment-letter', '/track-my-application',
  '/order-gcms-notes', '/how-to-read-gcms-notes', '/gcms-notes-analyzer', '/draw-results', '/express-entry-processing-times',
  '/noc-codes', '/get-started', '/pricing',
]);
function ogFor(path) {
  if (OG_ROUTES.has(path)) return `${SITE}/og${path}.png`;
  if (path.startsWith('/noc-codes/')) return `${SITE}/og/noc-codes.png`;
  if (path.startsWith('/draw-results/')) return `${SITE}/og/draw-results.png`;
  return OG_IMAGE;
}

function buildHead({ title, description, path, jsonLd }) {
  const url = `${SITE}${path}`;
  const og = ogFor(path);
  const tags = [
    `<meta name="description" content="${esc(description)}" data-prerender/>`,
    `<link rel="canonical" href="${esc(url)}" data-prerender/>`,
    `<meta property="og:type" content="website" data-prerender/>`,
    `<meta property="og:title" content="${esc(title)}" data-prerender/>`,
    `<meta property="og:description" content="${esc(description)}" data-prerender/>`,
    `<meta property="og:url" content="${esc(url)}" data-prerender/>`,
    `<meta property="og:image" content="${og}" data-prerender/>`,
    `<meta property="og:site_name" content="Mentor Visa" data-prerender/>`,
    `<meta name="twitter:card" content="summary_large_image" data-prerender/>`,
    `<meta name="twitter:title" content="${esc(title)}" data-prerender/>`,
    `<meta name="twitter:description" content="${esc(description)}" data-prerender/>`,
    `<meta name="twitter:image" content="${og}" data-prerender/>`,
  ];
  if (jsonLd) tags.push(`<script type="application/ld+json" data-prerender>${JSON.stringify(jsonLd)}</script>`);
  return tags.join('\n    ');
}

function renderPage({ title, description, path, jsonLd, bodyHtml }) {
  let html = template.replace(/<title>[^<]*<\/title>/, `<title>${esc(title)}</title>`);
  html = html.replace('</head>', `    ${buildHead({ title, description, path, jsonLd })}\n  </head>`);
  // Static content inside #root — replaced by React on mount (createRoot), read by crawlers before JS.
  html = html.replace('<div id="root"></div>', `<div id="root">${bodyHtml}</div>`);
  return html;
}

function writeRoute(path, html) {
  const dir = join(DIST, ...path.split('/').filter(Boolean));
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, 'index.html'), html);
}

const wrap = (inner) =>
  `<div style="max-width:860px;margin:0 auto;padding:48px 20px;font-family:Inter,system-ui,sans-serif;color:#1e293b;line-height:1.65">${inner}</div>`;

// ── 1. Static content routes ───────────────────────────────────────────────────
let count = 0;
for (const [path, meta] of Object.entries(ROUTE_META)) {
  const body = wrap(
    `<p style="font-size:.85rem"><a href="/" style="color:#4f46e5">Mentor Visa</a></p>` +
    `<h1>${esc(meta.h1)}</h1><p>${esc(meta.description)}</p>` +
    `<p><a href="/crs-calculator" style="color:#4f46e5">CRS Calculator</a> · <a href="/find-my-noc" style="color:#4f46e5">Find My NOC</a> · <a href="/noc-codes" style="color:#4f46e5">NOC Directory</a> · <a href="/draw-results" style="color:#4f46e5">Draw Results</a></p>`
  );
  writeRoute(path, renderPage({ ...meta, path, bodyHtml: body }));
  count++;
}

// ── 2. NOC detail pages (real content) ─────────────────────────────────────────
const nocData = JSON.parse(readFileSync(join(ROOT, 'public', 'noc-data.json'), 'utf-8'));
for (const [code, noc] of Object.entries(nocData)) {
  const teer = Number(code[1]);
  const cecEligible = teer <= 3;
  const path = `/noc-codes/${code}`;
  const title = `NOC ${code} — ${noc.title} | TEER ${teer} | Mentor Visa`;
  const description = `${cecEligible ? 'Eligible for Express Entry.' : 'Not CEC eligible.'} View official duties, immigration eligibility, TEER ${teer} info, and related occupations for NOC ${code} (${noc.title}).`;

  const groups = (noc.duty_groups && noc.duty_groups.length ? noc.duty_groups : [{ sub_title: null, duties: noc.duties || [] }]);
  const dutiesHtml = groups.map((g) =>
    (g.sub_title && groups.length > 1 ? `<h3>${esc(g.sub_title)}</h3>` : '') +
    `<ul>${(g.duties || []).map((d) => `<li>${esc(d)}</li>`).join('')}</ul>`
  ).join('');

  const body = wrap(
    `<p style="font-size:.85rem"><a href="/noc-codes" style="color:#4f46e5">← All NOC codes</a></p>` +
    `<h1>NOC ${esc(code)} — ${esc(noc.title)}</h1>` +
    `<p><strong>TEER ${teer}</strong> · ${esc(TEER_LABELS[teer] || '')} · ${cecEligible
      ? 'Eligible for Express Entry (CEC and FSWP)'
      : 'Not eligible for Express Entry (CEC/FSWP)'}</p>` +
    `<p>${esc(noc.lead_statement || '')}</p>` +
    `<h2>Main duties</h2>${dutiesHtml}` +
    `<h2>Check your eligibility</h2>` +
    `<p>Working in this occupation? <a href="/find-my-noc" style="color:#4f46e5">Verify your NOC code with AI</a>, ` +
    `<a href="/audit-employment-letter" style="color:#4f46e5">audit your employment letter</a> against these duties, or ` +
    `<a href="/crs-calculator" style="color:#4f46e5">calculate your CRS score</a>.</p>`
  );

  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'Occupation',
    name: noc.title,
    occupationalCategory: `NOC ${code}`,
    description: (noc.lead_statement || '').slice(0, 500),
    url: `${SITE}${path}`,
  };

  writeRoute(path, renderPage({ title, description, path, jsonLd, bodyHtml: body }));
  count++;
}

// ── 3. Per-draw pages (one URL per Express Entry round) ────────────────────────
// Slug formula must stay in sync with src/data/drawResults.ts drawSlug().
const DRAW_LABELS = {
  CEC: 'Canadian Experience Class', PNP: 'Provincial Nominee Program', French: 'French-Language Proficiency',
  Healthcare: 'Healthcare & Social Services', Trades: 'Trades', Education: 'Education', General: 'No Program Specified',
  Physicians: 'Physicians', 'Senior Managers': 'Senior Managers', STEM: 'STEM', Transport: 'Transport',
  Agriculture: 'Agriculture & Agri-Food', Military: 'Skilled Military Recruits',
};
const fmtDate = (iso) => new Date(iso + 'T00:00:00').toLocaleDateString('en-CA', { year: 'numeric', month: 'long', day: 'numeric' });
const draws = JSON.parse(readFileSync(join(ROOT, 'src', 'data', 'draw_results.json'), 'utf-8'));
const drawSlug = (d) => `${d.date}-${d.drawType.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
const drawPaths = [];
for (const d of draws) {
  const slug = drawSlug(d);
  const path = `/draw-results/${slug}`;
  drawPaths.push(path);
  const label = DRAW_LABELS[d.drawType] || d.drawType;
  const sameType = draws.filter(x => x.drawType === d.drawType);
  const idx = sameType.findIndex(x => drawSlug(x) === slug);
  const prev = sameType[idx + 1];
  const delta = prev ? d.crsScore - prev.crsScore : null;
  const title = `Express Entry Draw ${fmtDate(d.date)}: ${label} — CRS ${d.crsScore} | Mentor Visa`;
  const description = `IRCC issued ${d.itasIssued.toLocaleString('en-CA')} ITAs to ${label} candidates on ${fmtDate(d.date)} with a CRS cut-off of ${d.crsScore}${
    delta !== null ? ` (${delta > 0 ? 'up' : delta < 0 ? 'down' : 'unchanged'}${delta !== 0 ? ` ${Math.abs(delta)} points` : ''} vs the previous ${d.drawType} draw)` : ''
  }. Full details and CRS trend.`;
  const body = wrap(
    `<p style="font-size:.85rem"><a href="/draw-results" style="color:#4f46e5">← All draw results</a></p>` +
    `<h1>Express Entry Draw — ${esc(fmtDate(d.date))}: ${esc(label)}</h1>` +
    `<p>IRCC invited <strong>${d.itasIssued.toLocaleString('en-CA')}</strong> ${esc(label)} candidates to apply for permanent residence. ` +
    `The CRS cut-off was <strong>${d.crsScore}</strong>${delta !== null ? `, ${delta === 0 ? 'unchanged from' : `${Math.abs(delta)} points ${delta > 0 ? 'higher' : 'lower'} than`} the previous ${esc(label)} draw` : ''}.` +
    `${d.notes ? ` ${esc(d.notes)}` : ''}</p>` +
    `<p><a href="/crs-calculator" style="color:#4f46e5">Calculate your CRS score</a> · ` +
    `<a href="/draw-results" style="color:#4f46e5">All draw results</a> · ` +
    `<a href="/find-my-noc" style="color:#4f46e5">Find your NOC code</a></p>`
  );
  const jsonLd = {
    '@context': 'https://schema.org', '@type': 'Article',
    headline: `Express Entry ${label} draw — ${fmtDate(d.date)}`,
    datePublished: d.date, description,
    author: { '@type': 'Organization', name: 'Mentor Visa', url: SITE },
    publisher: { '@type': 'Organization', name: 'Mentor Visa', logo: { '@type': 'ImageObject', url: `${SITE}/logo.png` } },
    mainEntityOfPage: `${SITE}${path}`,
  };
  writeRoute(path, renderPage({ title, description, path, jsonLd, bodyHtml: body }));
  count++;
}

// ── 4. Sitemap: generated here so it can never drift from the real routes ──────
{
  const today = new Date().toISOString().slice(0, 10);
  const urls = [
    { loc: '/', freq: 'weekly', pri: '1' },
    ...Object.keys(ROUTE_META).map(p => ({ loc: p, freq: 'weekly', pri: '0.9' })),
    ...drawPaths.map(p => ({ loc: p, freq: 'monthly', pri: '0.7' })),
    ...Object.keys(nocData).map(c => ({ loc: `/noc-codes/${c}`, freq: 'monthly', pri: '0.7' })),
    { loc: '/privacy-policy', freq: 'yearly', pri: '0.3' },
    { loc: '/terms-of-service', freq: 'yearly', pri: '0.3' },
    { loc: '/refund-policy', freq: 'yearly', pri: '0.3' },
  ];
  const xml = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
    urls.map(u => `  <url>\n    <loc>${SITE}${u.loc}</loc>\n    <lastmod>${today}</lastmod>\n    <changefreq>${u.freq}</changefreq>\n    <priority>${u.pri}</priority>\n  </url>`).join('\n') +
    `\n</urlset>\n`;
  writeFileSync(join(DIST, 'sitemap.xml'), xml);
  console.log(`Sitemap: ${urls.length} URLs written to dist/sitemap.xml`);
}

// ── 5. Homepage fallback meta (data-rh so Helmet replaces per page) ────────────
// The SPA fallback (dist/index.html) serves the homepage AND unprerendered app routes;
// description/OG here are homepage defaults; NO canonical (each page sets its own via Helmet).
{
  const title = 'Mentor Visa — AI-Powered Tools for Canadian Express Entry';
  const description = 'Free CRS Calculator, NOC Finder, Employment Letter Auditor, and 70+ term Immigration Glossary. AI-powered tools to help you navigate Canadian Express Entry with confidence.';
  const tags = [
    `<meta name="description" content="${esc(description)}" data-prerender/>`,
    `<meta property="og:type" content="website" data-prerender/>`,
    `<meta property="og:title" content="${esc(title)}" data-prerender/>`,
    `<meta property="og:description" content="${esc(description)}" data-prerender/>`,
    `<meta property="og:url" content="${SITE}" data-prerender/>`,
    `<meta property="og:image" content="${OG_IMAGE}" data-prerender/>`,
    `<meta property="og:site_name" content="Mentor Visa" data-prerender/>`,
    `<meta name="twitter:card" content="summary_large_image" data-prerender/>`,
  ].join('\n    ');
  writeFileSync(join(DIST, 'index.html'), template.replace('</head>', `    ${tags}\n  </head>`));
}

console.log(`Prerendered ${count} routes into dist/ (${Object.keys(nocData).length} NOC + ${draws.length} draws + ${Object.keys(ROUTE_META).length} content pages) + homepage fallback meta.`);
