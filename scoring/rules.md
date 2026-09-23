# Atriz ICP Scoring — Full Rule Reference

This is the complete rule set behind `scripts/score_opportunities.py`,
organized by topic. The script is the source of truth for exact matching
logic; this doc explains *why* each rule exists. Read the relevant section
before changing anything — several rules exist specifically because an
earlier, simpler version produced a wrong result on real data.

## Pipeline stages, in order

1. **Flatten** each record (see field mapping in SKILL.md).
2. **Dedup pass 1**: by `job_id` (top-level `id`, fallback `apply_url`),
   keep first occurrence.
3. **Removal filters** (rows are dropped entirely, not just downgraded):
   - `job_category` OR `title` contains `\bsales\b` (word-boundary — does
     NOT match "Salesforce").
   - `organization_type` is "Non-Profit" or "Government".
4. **Niche classification** (first match wins, checked in this precedence
   order to prevent cross-niche bleed — see "Niche classification order"
   below).
5. **Blue-collar/production-floor pre-filter** — overrides to Ignore,
   niche forced to "None".
6. **Hard overrides** (force priority to Ignore regardless of numeric
   score; row stays in output with the reason in `flags` for audit) — see
   "Hard-pass categories" below.
7. **Point deductions** (adjust score, don't override priority) — see
   "Point deductions" below.
8. **Flag-only notes** (no score impact) — non-US HQ, ambiguous bare
   senior title.
9. **Numeric scoring** — see "Scoring model" below.
10. **Priority label** from final score, unless a hard override already
    forced Ignore.
11. **Dedup pass 2**: by `company_name` + `title` + `workplace_location`
    (case-insensitive, trimmed), keep first occurrence. Note: two postings
    for the same role at the same company with different job IDs and
    slightly different location-list orderings are NOT deduped — that's
    two real postings, not a dedup bug (confirmed via Upshop and Paystand
    both posting the same role to two job boards).
12. **Sort**: score descending, then company name ascending.

## Scoring model (exact weights)

- **Company Size Fit**: nb_employees <100 = +1, 100-500 = +2, 500-1000 =
  +1.5, >1000 = +0 (but >1000 also triggers the hard "Ignore immediately"
  override below, independent of this score).
- **Industry Fit**: Fintech or Financial Technology = +2, DTC = +2,
  SaaS/Software/eCommerce/IT/Marketing = +1, else 0.
- **Location Fit**: Florida, Texas, Utah, Colorado, Illinois, or Tennessee
  named in `workplace_location` = +1, else 0. Also matches specific cities
  that appear without a state name: the FL cities (Miami, Orlando, Tampa,
  Jacksonville, Fort Lauderdale, St. Petersburg, Tallahassee, Boca Raton,
  West Palm Beach) and Chicago (for Illinois). No other city fallbacks
  exist for TX/UT/CO/TN yet — add them only if Tony confirms he wants that
  treatment extended (e.g. Austin/Dallas/Houston, Denver, Nashville,
  Salt Lake City).
- **Margin Proxy**: SaaS/Software or Fintech/Financial Technology = +2,
  Marketing or Services = +1, else 0.
- **Funding Amount**: >$100M = +1.5, $10-100M = +1, $1-9.99M = +0.5, <$1M
  = +0.25, missing = 0.
- **Funding Recency**: 2024+ = +0.5, 2022-2023 = +0.25, before 2022 or
  missing = 0.
- **Funding Type**: Series A-D, Growth, or Private Equity = +0.5;
  Corporate, Venture, or Seed Round = +0.25; Grant, Debt, or Angel = 0.
  Anything else (e.g. "Series E+", "Funding Round") is ambiguous — scores
  0 and gets flagged for review rather than guessed at.
- **Niche Fit**: matched niche = +2, else 0. (Note: this only zeros out
  when niche="None" — it does not override the other six dimensions. See
  "niche=None can still score high" under Known Limitations.)
- **Seniority Fit**: C-Suite = +3, VP = +2, Director = +1.5, Manager =
  +0.5, IC = 0. Keyword match on title, falling back to `apply_url` when
  the title field strips the seniority prefix.

Priority: nb_employees > 1000 = Ignore immediately (independent of
score); else score ≥10 = ICP, 7-9.99 = Qualified, 4-6.99 = Marginal,
0-3.99 = Ignore. Any hard override (see below) forces Ignore regardless
of the numeric score.

## Niche classification order

Checked in this order, first match wins, to prevent cross-niche bleed:

1. **Ops & Supply Chain** — catches "Director of Operations" etc. before
   Product/Tech or GTM can grab it.
2. **Revenue & GTM** — checked before generic Ops matching so "Revenue
   Operations" titles don't fall into Ops.
3. **Data & Analytics** — with the geospatial-data-engineer exception
   (see below).
4. **Product & Tech** — bare "Product Manager" still counts as a match
   but is flagged/deducted (see Point Deductions).
5. **Performance Marketing** — exact role titles first, then the broader
   keyword net (Ecommerce/Lifecycle Marketing/Email Marketing).
6. Else: **"None"**.

Every phrase check uses `\b` word-boundary regex, and checks each field
(`job_category`, `title`) independently — never concatenated together.
See "Bug history" for why both of those matter.

### Niche role-scope lists (Michael's exact lists)

- **Performance Marketing** — management: VP of Growth, Director of
  Demand Gen, CMO, Marketing Director, VP of Marketing, Head of
  Marketing, Head of Growth, Digital Marketing Director, VP of Demand
  Generation, Director of Growth Marketing, Head of Paid Media, Chief
  Growth Officer, Director of Marketing. IC: Media Buyer, Performance
  Creative. Broader net (not exact titles, cast wider): Performance
  Marketing, Ecommerce, Lifecycle Marketing, Email Marketing.
- **Data & Analytics** — management: VP of Credit Risk, SVP of Data
  Analytics, VP of Data Science, Head of AI, VP of Analytics, Director
  of Data Science, Head of Data, Director of Analytics, Chief Data
  Officer. IC: Analytics Engineer, Data Analyst, Data Scientist, Data
  Engineer (except "Geospatial Data Engineer" — specialized/geospatial
  titles don't count despite containing "Data Engineer").
- **Revenue & GTM** — management: VP of Sales, Director of Sales, VP of
  Revenue, Chief Revenue Officer, Head of Sales, Director of Revenue
  Operations, VP of Business Development. IC: Producer, Revenue
  Operations Analyst. (Note: VP/Director/Head of Sales titles match this
  list but still get removed by the sales-title filter — see Pipeline
  stage 3.)
- **Product & Tech** — management: Head of Product, VP of Product,
  Director of Product, CPO, CTO, VP of Engineering, Director of
  Engineering, Engineering Manager, VP of Technology, Director of
  Technology, Head of Engineering. IC: Staff Engineer, Principal
  Engineer, Senior/Group/Lead/Staff/Principal Product Manager. Bare
  "Product Manager" (no qualifier) still matches but takes a -1.0
  deduction.
- **Ops & Supply Chain** — Director/VP/Head/COO-level Operations,
  Procurement, Supply Chain, and Logistics titles, plus the
  "Supply Chain / Logistics / Procurement" `job_category`. This niche is
  currently hands-off/out of active scope — matching it forces an Ignore
  override, but it's still classified (not "None") so it's distinguishable
  in the data from a genuine non-match.

**Title normalization applied before all phrase matching**: "Vice
President" → "VP", and "Demand Generation" → "Demand Gen". Both are pure
synonym fixes (same role, different spelling), not scope changes — see
Bug History.

**Comma-format titles remain unmatched by design** — e.g. "Vice
President, Marketing" doesn't match "VP of Marketing", and "Director,
Data Engineering" doesn't match "Director of Data Science". Expanding
this would require deciding whether Michael's exact-list niches should
tolerate comma format too, which hasn't been confirmed. Surface these as
near-misses (see SKILL.md) rather than auto-matching.

## Hard-pass categories (force Ignore, row stays in output for audit)

Checked against BOTH `industries` and `activities` fields independently
(a company can be, say, a PR business without "Public Relations" being
one of its `industries` tags — it might only show up in `activities`).

- **Religious organizations**
- **Sports/hospitality** — industries tags (sports, hospitality, hotel,
  restaurant, recreation, resort, casino, golf, country club) as primary
  signal; company-name fallback (hotel, resort, casino, inn, lodge) only
  when industries AND activities are both empty (sparse-data case); plus
  specific multi-word activity phrases ("hotel operations", "resort
  management") rather than the bare word "sports" (see Bug History for
  why bare "sports" was removed).
  - **Exception**: tech-vendor signal present (technology, software,
    saas, platform in industries or activities) → company sells TO the
    industry, isn't an operator IN it. Flagged, not excluded.
- **Franchise/MLM** — franchise, franchisee, multi-level marketing, mlm
  in industries/activities/company name.
  - **Exception**: corporate-side franchisor language in activities
    (franchise development, franchise business development, franchise
    management, franchise business management, brand management, brand
    licensing, brand operations, business management consulting) → this
    is the reachable corporate entity, not an individual franchisee
    location. Flagged, not excluded.
- **PR firms/agencies** — public relations, PR agency, PR firm.
  - **Exception**: 2+ signals from digital marketing/media buying/
    advertising/social media/SEO/media planning/brand identity/marketing
    consulting/graphic design in activities, AND nb_employees is known
    and ≤1000 → real diversification beyond core PR. Takes the ordinary
    general-agency -1.5 deduction instead of a full exclusion. Unknown
    employee count stays conservative (excluded) since a giant ad-holding
    company with missing headcount data shouldn't slip through on the
    diversification signal alone.
- **Healthcare services/care-delivery** — broad category: hospitals,
  senior living/assisted living/memory care, nursing, medical practices,
  primary/urgent care, mental health/psychiatric/behavioral health, home
  health/hospice, clinic, rehabilitation.
  - **Exception**: same tech-vendor signal as sports/hospitality.
  - **Distinct from** medical device/biotech manufacturers, which are
    product companies, not care-delivery providers — see Point
    Deductions/flags (this distinction is unconfirmed by Tony/Michael).
- **Clinical research organizations**
- **Pharmaceutical companies**
- **Educational institutions** — schools, universities, colleges,
  academies, K-12.
- **Traditional/community banks** — brick-and-mortar retail banking
  (e.g. Republic Bank of Chicago), distinct from fintech. Triggers on a
  bare "Banking" industries tag, retail/commercial/consumer/community/
  branch-banking or deposit-account-management activity phrases, or
  "bank"/"credit union"/"trust company" in the company name.
  - **Exception**: fintech/financial-technology tag or tech-vendor signal
    present → not excluded.
  - **Does NOT match** a bare "Investment Banking" tag — investment
    banking (M&A advisory, capital markets) is a different business from
    retail/community banking. The phrase "investment banking" is
    stripped before checking for the bare word "banking", so a company
    needs an actual separate "Banking" tag (or the other signals) to be
    excluded. (See Bug History — this and the mortgage-lending signal
    were both false-positive sources.)
- **Current clients** — see `/areas/current-clients.md` in memory for
  the live list (currently: One Park Financial). Keep this script's
  `CURRENT_CLIENTS` list in sync with that memory file whenever a new
  client signs.
- **Ops & Supply Chain niche match** — hands-off/out of active scope
  (see niche list above).
- **nb_employees > 1000** — independent backstop; this alone catches
  most mega-brands regardless of whether a category exception above
  would otherwise have applied (e.g. Marriott 411k employees is Ignored
  by headcount even though the franchise exception logic would otherwise
  need to evaluate it).

## Point deductions (adjust score, don't force Ignore)

- **General (non-PR) agency**: -1.5. Checked against both industries and
  activities (this dataset's industries taxonomy never uses the literal
  word "agency" — see Bug History). No employee-count or revenue gate
  currently exists for this one (unlike the dev-shop deduction) since
  revenue isn't available in the export data.
- **Small software dev shop, low revenue**: -1.5. Requires nb_employees
  < 50 in addition to the dev-shop industry/activity signal (see Bug
  History for why the employee gate was added).
- **Bare "Product Manager" title** (no Senior/Group/Lead/Staff/Principal
  qualifier): -1.0. Still counts as a Product & Tech niche match.
- **In-niche title that doesn't match the exact defined role list, but
  does match a niche's broader keyword net** (currently only Performance
  Marketing has one): -0.5, flagged for manual review.

## Flag-only (no score impact)

- **Non-US HQ**: flagged for review, not penalized on its own. Confirmed
  via Simpro Group (HQ Australia) being approved as a good opportunity —
  non-US HQ is only a negative signal in combination with other weak
  factors, not an automatic downgrade.
- **Ambiguous bare senior title** (title is exactly "Director", "Vice
  President", or "VP" with no functional descriptor): flagged for manual
  research rather than auto-classified/scored with confidence.
- **Medical device/biotech manufacturer signal** (not care-delivery):
  flagged for manual confirm-in-scope, not excluded — this distinction
  from the healthcare-services hard pass has not been explicitly
  confirmed by Tony/Michael.
- **Sports/hospitality or healthcare signal that looks like a tech
  vendor**: flagged alongside the "not excluded" note when the
  tech-vendor exception fires, so it's visible in the audit trail.

## Known limitations (surface to Tony, don't silently "fix")

- **Comma-format titles** don't match exact-phrase niche lists (see
  above) — this is deliberate pending confirmation, not a bug.
- **niche="None" rows can still score into Qualified/ICP** on
  company-level fit alone (size, industry, funding, location) even when
  the specific role is nowhere near Atriz's five niches — e.g. an
  "Executive Assistant" posting at a well-funded Utah SaaS company scored
  7.5/Qualified. Worth a manual skim of niche="None" rows in the
  Qualified+ tier each run, not just the near-miss Director+/VP+ list.
- **Medical device/biotech vs. care-delivery** distinction (see above) is
  unconfirmed.

## Bug history (context for why the current logic looks the way it does)

- **"Director" scored as C-Suite**: plain substring matching on short
  acronyms (cto, cpo, cmo, ceo, coo, cfo) meant "director" matched "cto"
  (di-re**cto**-r), scoring every Director title as C-Suite (+3.0 instead
  of +1.5). Fixed with `\b` word-boundary regex — this bug class recurred
  later in the niche-classification lists too (same fix applied there).
- **Field-boundary bleed**: concatenating fields together for keyword
  matching (e.g. `job_category + " " + title`) can spell out an
  unintended phrase at the join point — "Business Operations" + "Director
  of Revenue Operations" concatenated into "...Operations Director..."
  and wrongly matched Ops & Supply Chain's "operations director" phrase,
  mis-scoring a real GTM role (Roo's "Director of Revenue Operations") as
  hands-off Ops. Fixed by checking every field independently, never
  concatenated.
- **`\bsales\b` vs "Salesforce"**: plain substring "sales" matching
  wrongly excluded 6 real Salesforce-titled Product/Tech roles. Fixed
  with word-boundary regex (confirmed it does not match "Salesforce").
- **Tech-vendor exception gaps**: needed "technology" added as a bare
  signal word (caught Mad Mobile, tagged "Hospitality Technology"), and
  needed to also apply to the healthcare override, not just sports/
  hospitality (caught Roo, a vet-staffing marketplace whose activities
  literally contained "hospital").
  - **Mortgage lending false positives** (traditional-bank exclusion):
    "mortgage lending" as a bank-activity signal wrongly caught
    PulteGroup (homebuilder with an in-house mortgage arm), NewPoint Real
    Estate Capital (a CRE lender), and Acrisure (an insurance brokerage
    with a lending arm) — none are retail banks. Removed the signal
    entirely rather than trying to scope it further.
  - **Investment Banking false positive** (traditional-bank exclusion):
    a bare "Investment Banking" industries tag triggered the rule via
    the substring "banking". Fixed by stripping "investment banking"
    from the check text before looking for the bare word "banking".
- **"General agency" deduction only checked `industries`**: this
  dataset's industries taxonomy never uses the literal word "agency" —
  it only shows up in free-text `activities`. Fixed to check both
  fields.
- **"Small dev shop" deduction had no size gate**: first version flagged
  any dev-shop-language company regardless of size, including Microsoft
  (220,000 employees). Added the nb_employees < 50 gate to match the
  actual intent ("small... shop").
- **Bare "sports" word matching**: caused false positives (a marketing
  agency doing "sports marketing", a kids' fitness franchise with "youth
  sports" programs) — neither is a sports/hospitality organization
  itself. Narrowed to industries-tag matching plus specific multi-word
  activity phrases.
- **VP/Vice President and Demand Gen/Demand Generation gaps**: exact-
  phrase lists were written with abbreviations ("VP of X", "Demand Gen")
  and missed postings spelling them out in full. Fixed with title
  normalization applied before all phrase matching (see Niche
  classification order above).

## Not scorable with current export data

Family ownership/margin combination, recent CEO change, known existing
relationships, and exact revenue figures don't exist as fields in the
Apify export — these remain manual-review-only qualitative notes, not
automated point values.

## v2 changes (2026-09-23): restaurants, senior care, sales titles, automation

Requested by Tony: "take out the senior caring houses and the restaurants."

- **Plural-tag bug (root cause of restaurant/hotel leaks).** Phrase matching
  uses `\b` word boundaries, so `hotel` does not match the dataset's actual
  tag `Hotels & Resorts`, and `restaurant` does not match `Restaurants`.
  Hotels, resorts, and restaurant operators were leaking into Marginal as a
  result. Plural forms are now listed explicitly (`hotels`, `resorts`,
  `casinos`, `country clubs`). Tested on the 09-14 batch: DoubleTree, Starwood
  Hotels, Auberge Resorts, Little Palm Island Resort, New Waterloo, MML
  Hospitality, Bridgeton, Pearl, CUSA and Subway went from Marginal to Ignore.
  On the 04-16 batch: Naked Farmer and Chick-fil-A Palm Coast.
- **Restaurants are now their own hard pass**, separate from sports/hospitality.
  A company is excluded when it has (a) a restaurant-style company name
  (restaurant, pizzeria, steakhouse, grill, bistro, taqueria, cantina, eatery,
  brewpub, chick-fil-a), (b) operator language in activities ("restaurant and
  bar operations", "operating fast-casual restaurants", "drive-thru
  operations", "fine dining operations", …), or (c) a Restaurants / Fast Food /
  Quick Service Restaurants / Bars / Coffee & Snack Shops / Food Service
  industries tag.
- **Senior care is now its own hard pass**, separate from general healthcare.
  The signals are senior living, assisted living, memory care, skilled
  nursing, long-term care, home care, in-home/companion care, adult day,
  retirement communities, nursing homes, and elder care. They are checked
  across name, activities, and industries. Well-known home-care franchise
  brand names are in the name list (Home Instead, Comfort Keepers, Visiting
  Angels, Right at Home).
- **Tech-vendor exception for both new rules (strict version).** This follows
  the Sauce/Roo/Mad Mobile precedent: companies that sell technology *to*
  restaurants or senior care are kept and flagged, not excluded.
  - An industries-tag hit is rescued by a Software/SaaS/Technology/Platforms
    industries tag, or by "software"/"saas" in activities. Dorsia, Mad Mobile,
    and Workstream stay in.
  - An operator-activity hit is rescued only when the activities themselves
    say software/SaaS ("senior living software"). A Software & SaaS
    industries tag alone does not rescue a company whose activities say
    "Restaurant and bar operations" (the Topgolf case).
  - A company-name hit is never rescued.
  - Deliberately NOT excluded: food & beverage *manufacturers* and CPG (Built
    Bar), restaurant-equipment manufacturers (Front of the House), grocery
    retail (Earth Fare), and property managers whose portfolio includes some
    senior housing (ConcordRENTS). "Food & Beverage" and "catering" are not
    used as signals for that reason.
- **Sales-title removal is off by default.** This matches Tony's standing call
  since 09-08. Sales-titled rows are kept, scored normally, and flagged
  "sales title - kept". Set the env var `REMOVE_SALES_TITLES=true` to restore
  the old removal.
- **Current clients** now live in `config/current_clients.txt`, one name per
  line, so the list can be edited without touching code.
- **Summary output.** With the env var `SUMMARY_JSON=path`, the script also
  writes a JSON summary: funnel counts, priority/niche breakdown, top 25
  ICP/Qualified rows, the niche=None Qualified+ count, and the
  restaurant/senior-care exclusion counts. The weekly email uses it.
