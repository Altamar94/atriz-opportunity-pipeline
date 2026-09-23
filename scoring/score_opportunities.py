#!/usr/bin/env python3
"""
Atriz ICP opportunity scoring pipeline.

Usage:
    python score_opportunities.py OUTPUT.csv INPUT1.json [INPUT2.json ...]

Takes one or more hiring-cafe/Apify JSON exports (each a JSON list of job
records with `id`, `apply_url`, `v5_processed_job_data`, and
`enriched_company_data`), runs them through Atriz's full ICP scoring
pipeline, and writes one sorted, deduplicated, scored CSV.

See references/rules.md in this skill for the full rationale and change
history behind every rule below. If Tony gives a new rule or exception,
update BOTH this script and references/rules.md together.
"""
import json, re, csv, sys, os, collections

def has_phrase(text, phrase):
    if not text:
        return False
    text = normalize_title(text)
    return re.search(r'\b' + re.escape(phrase.lower()) + r'\b', text.lower()) is not None

def normalize_title(text):
    if not text:
        return text
    # "Vice President" / "VP" and "Demand Generation" / "Demand Gen" are the
    # same seniority/role written two ways - normalize so exact-phrase lists
    # written with one spelling also catch postings using the other.
    text = re.sub(r'\bvice president\b', 'vp', text, flags=re.IGNORECASE)
    text = re.sub(r'\bdemand generation\b', 'demand gen', text, flags=re.IGNORECASE)
    return text

def any_phrase(text, phrases):
    return any(has_phrase(text, p) for p in phrases)

def joined(lst):
    return ' | '.join(lst) if lst else ''

FL_CITIES = ['miami','orlando','tampa','jacksonville','fort lauderdale',
             'st. petersburg','st petersburg','tallahassee','boca raton',
             'west palm beach']
LOCATION_STATES = ['florida','texas','utah','colorado','illinois','tennessee']
LOCATION_CITY_FALLBACKS = FL_CITIES + ['chicago']

# ---------------- niche role scope lists (Michael's exact lists) ----------------
OPS_TITLE_PHRASES = [
    'director of operations','vp of operations','vice president of operations',
    'head of operations','svp of operations','evp of operations',
    'chief operating officer','director of procurement','vp of procurement',
    'head of procurement','director of supply chain','vp of supply chain',
    'head of supply chain','director of logistics','vp of logistics',
    'head of logistics','site operations','plant operations',
    'manufacturing operations director','warehouse operations director',
]
OPS_JOB_CATEGORIES = ['supply chain / logistics / procurement']

GTM_MGMT = ['vp of sales','director of sales','vp of revenue','chief revenue officer',
            'head of sales','director of revenue operations','vp of business development']
GTM_IC = ['producer','revenue operations analyst']

DATA_MGMT = ['vp of credit risk','svp of data analytics','vp of data science','head of ai',
             'vp of analytics','director of data science','head of data',
             'director of analytics','chief data officer']
DATA_IC = ['analytics engineer','data analyst','data scientist','data engineer']
DATA_IC_EXCEPTIONS = ['geospatial data engineer']

PRODUCT_MGMT = ['head of product','vp of product','director of product','cpo','cto',
                'vp of engineering','director of engineering','engineering manager',
                'vp of technology','director of technology','head of engineering']
PRODUCT_IC = ['staff engineer','principal engineer','senior product manager',
              'group product manager','lead product manager','staff product manager',
              'principal product manager']
BARE_PM = 'product manager'
PM_QUALIFIERS = ['senior product manager','group product manager','lead product manager',
                  'staff product manager','principal product manager']

MKT_MGMT = ['vp of growth','director of demand gen','cmo','marketing director',
            'vp of marketing','head of marketing','head of growth',
            'digital marketing director','vp of demand generation',
            'director of growth marketing','head of paid media',
            'chief growth officer','director of marketing']
MKT_IC = ['media buyer','performance creative']
MKT_BROAD = ['performance marketing','ecommerce','e-commerce','lifecycle marketing','email marketing']

BLUE_COLLAR_CATEGORIES = [
    'skilled trades - manufacturing and industrial','skilled trades - maintenance and repair',
    'skilled trades - construction','skilled trades - mechanical and electrical',
    'skilled trades - general labor','custodial services',
]
BLUE_COLLAR_TITLE_KEYWORDS = ['machine operator','forklift operator','custodian','janitor']

# ---------------- hard-pass signal lists ----------------
RELIGIOUS_SIGNALS = ['religious','church','ministry','ministries','diocese','ecclesiastical',
                      'ecclesial','ecclesia','synagogue','parish','ecumenical']
# Plurals listed explicitly: \b word matching means 'hotel' does NOT match the
# dataset's actual tag "Hotels & Resorts" (v2 fix - see rules.md).
SPORTS_HOSP_INDUSTRY = ['sports','hospitality','hotel','hotels','recreation','resort','resorts',
                         'casino','casinos','golf','country club','country clubs']
SPORTS_HOSP_NAME_FALLBACK = ['hotel','resort','casino','inn','lodge']
SPORTS_HOSP_ACTIVITY_PHRASES = ['hotel operations','resort management']
TECH_VENDOR_SIGNALS = ['technology','software','saas','platform']

# ---------------- v2: restaurants (own hard pass, split out of sports/hospitality) ----------------
RESTAURANT_INDUSTRY = ['restaurant','restaurants','fast food','quick service restaurants',
                       'bars','bars & nightlife','coffee & snack shops','food service','foodservice']
RESTAURANT_OPERATOR_ACTIVITY = ['restaurant operation','restaurant operations',
                                'restaurant and bar operations','restaurant and bar management',
                                'restaurant operation and management','full-service restaurant',
                                'full-service restaurant operation','fast-casual restaurants',
                                'operating fast-casual restaurants','operating restaurants',
                                'quick-service restaurant','quick-service restaurants',
                                'fine dining operations','fine dining restaurant services',
                                'fine dining restaurant','casual dining','drive-thru operations',
                                'sandwich preparation','bar and lounge operation',
                                'hotel bar and lounge operation','coffee shop management',
                                'coffee shop operation','bakery cafe']
RESTAURANT_NAME = ['restaurant','restaurants','pizzeria','steakhouse','grill','bistro',
                   'taqueria','cantina','eatery','brewpub','chick-fil-a']

# ---------------- v2: senior care (own hard pass, split out of healthcare) ----------------
SENIOR_CARE_INDUSTRY = ['senior living','senior care','senior care services','assisted living',
                        'memory care','skilled nursing','long-term care','home care',
                        'retirement communities','retirement community','elder care','eldercare',
                        'continuing care','nursing homes','nursing home']
SENIOR_CARE_OPERATOR_ACTIVITY = ['senior living','senior living communities','senior living community',
                                 'senior living operations','senior care','senior care services',
                                 'assisted living','assisted living communities','assisted living facility',
                                 'memory care','memory care services','skilled nursing',
                                 'skilled nursing care','skilled nursing facility','long-term care',
                                 'independent living','continuing care retirement',
                                 'retirement community','retirement communities','adult day care',
                                 'adult day services','in-home care','in-home care services',
                                 'home care services','non-medical home care','companion care',
                                 'caregiver services','elder care','eldercare','hospice care',
                                 'respite care','nursing home']
SENIOR_CARE_NAME = ['senior living','senior care','assisted living','memory care',
                    'retirement community','retirement living','home care','homecare',
                    'nursing home','nursing center','rehabilitation center','care center',
                    'healthcare center','health care center','elder care','eldercare',
                    'home instead','comfort keepers','visiting angels','right at home']
# Narrower vendor test for the two v2 rules: only a software/SaaS/technology
# INDUSTRIES tag, or 'software'/'saas' in activities, counts as "sells tech to
# the industry". Bare 'platform' in activities is not enough (an operator can
# mention its own "online ordering platform").
STRICT_VENDOR_INDUSTRY = ['software','saas','technology','platforms']
STRICT_VENDOR_ACTIVITY = ['software','saas']
FRANCHISE_SIGNALS = ['franchise','franchisee','multi-level marketing','mlm']
FRANCHISE_CORP_EXCEPTION_PHRASES = ['franchise development','franchise business development',
                                     'franchise management','franchise business management',
                                     'brand management','brand licensing','brand operations',
                                     'business management consulting']
PR_SIGNALS = ['public relations','pr agency','pr firm']
PR_DIVERSIFICATION_SIGNALS = ['digital marketing','media buying','advertising','social media',
                               'seo','media planning','brand identity','marketing consulting',
                               'graphic design']
HEALTHCARE_CAREDELIVERY_SIGNALS = ['hospital','hospitals','senior living','assisted living','memory care',
                       'nursing','medical practice','primary care','urgent care',
                       'mental health','psychiatric','behavioral health','home health',
                       'hospice','clinic','rehabilitation','healthcare services','healthcare']
MEDICAL_DEVICE_SIGNALS = ['medical device','medical devices','biotech','biotechnology',
                           'medical technology','orthopedic','surgical instrument',
                           'diagnostic equipment manufacturing','biopharmaceutical manufacturing',
                           'tissue product']
CLINICAL_RESEARCH_SIGNALS = ['clinical research','cro ']
PHARMA_SIGNALS = ['pharmaceutical','pharma']
EDUCATION_SIGNALS = ['education','school','university','college','academy','k-12']
GENERAL_AGENCY_SIGNALS = ['agency','agencies']
DEV_SHOP_SIGNALS = ['software development','custom software','app development','dev shop']

# Companies to always exclude regardless of fit - keep this in sync with
# /areas/current-clients.md in memory. Update BOTH when a new client signs.
def _load_current_clients():
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.environ.get('CURRENT_CLIENTS_FILE') or '',
                 os.path.join(here, '..', 'config', 'current_clients.txt')):
        if path and os.path.exists(path):
            with open(path) as fh:
                names = [l.strip().lower() for l in fh
                         if l.strip() and not l.strip().startswith('#')]
            if names:
                return names
    return ['one park financial']
CURRENT_CLIENTS = _load_current_clients()

# Sales-title removal is PAUSED (Tony, 2026-09-08, reconfirmed every batch through
# 09-21): sales-titled rows are kept, scored normally, and flagged. Set env var
# REMOVE_SALES_TITLES=true to restore the old hard removal.
REMOVE_SALES_TITLES = os.environ.get('REMOVE_SALES_TITLES', 'false').strip().lower() in ('1','true','yes')

TRADITIONAL_BANK_ACTIVITY_SIGNALS = ['retail banking','commercial banking','consumer banking',
                                      'community banking','branch banking','deposit account management']
TRADITIONAL_BANK_NAME_SIGNALS = ['credit union','bank','trust company','savings bank',
                                  'national bank','federal savings']

def is_tech_vendor(industries_text, activities_text):
    return any_phrase(industries_text, TECH_VENDOR_SIGNALS) or any_phrase(activities_text, TECH_VENDOR_SIGNALS)

def is_strict_tech_vendor(industries_text, activities_text):
    return any_phrase(industries_text, STRICT_VENDOR_INDUSTRY) or any_phrase(activities_text, STRICT_VENDOR_ACTIVITY)

def category_override(name, industries_text, activities_text, industry_sig, operator_sig, name_sig):
    """Shared logic for the v2 restaurant / senior-care hard passes.
    Returns ('exclude'|'vendor'|None, how).
    - company-name pattern            -> exclude
    - operator language in activities -> exclude, unless the activities themselves
      say software/SaaS (e.g. "senior living software", "restaurant HR software")
    - industries tag only             -> exclude, unless software/SaaS/technology shows
      in industries or activities (Dorsia, Mad Mobile precedent)
    Note: a software/SaaS INDUSTRIES tag alone is not enough to rescue an
    operator-language hit (Topgolf: "Software & SaaS" tag, but activities say
    "Restaurant and bar operations").
    """
    if any_phrase(name, name_sig):
        return 'exclude', 'company name'
    if any_phrase(activities_text, operator_sig):
        if any_phrase(activities_text, STRICT_VENDOR_ACTIVITY):
            return 'vendor', 'operator activity'
        return 'exclude', 'operator activity'
    if any_phrase(industries_text, industry_sig):
        if is_strict_tech_vendor(industries_text, activities_text):
            return 'vendor', 'industries tag'
        return 'exclude', 'industries tag'
    return None, ''

def niche_classify(f):
    title = f['title']
    cat = f['job_category']
    flags = []
    ded = 0.0

    if any_phrase(title, OPS_TITLE_PHRASES) or cat.lower() in OPS_JOB_CATEGORIES:
        return 'Ops & Supply Chain', ded, flags

    if any_phrase(title, GTM_MGMT) or any_phrase(title, GTM_IC):
        return 'Revenue & GTM', ded, flags

    if any_phrase(title, DATA_MGMT):
        return 'Data & Analytics', ded, flags
    if any_phrase(title, DATA_IC):
        if any_phrase(title, DATA_IC_EXCEPTIONS):
            pass
        else:
            return 'Data & Analytics', ded, flags

    if any_phrase(title, PRODUCT_MGMT) or any_phrase(title, PRODUCT_IC):
        return 'Product & Tech', ded, flags
    if has_phrase(title, BARE_PM) and not any_phrase(title, PM_QUALIFIERS):
        ded -= 1.0
        flags.append('bare "Product Manager" title, below IC scope: -1.0')
        return 'Product & Tech', ded, flags

    if any_phrase(title, MKT_MGMT) or any_phrase(title, MKT_IC):
        return 'Performance Marketing', ded, flags
    if any_phrase(title, MKT_BROAD):
        ded -= 0.5
        flags.append('non-exact title match within niche broad net: -0.5')
        return 'Performance Marketing', ded, flags

    return 'None', ded, flags

def is_blue_collar(f):
    cat = f['job_category'].lower()
    title = f['title']
    if cat in BLUE_COLLAR_CATEGORIES:
        return True
    if any_phrase(title, BLUE_COLLAR_TITLE_KEYWORDS):
        return True
    return False

CSUITE_TOKENS = ['cto','cpo','cmo','ceo','coo','cfo','cro','chief']
def seniority_fit(title, apply_url):
    text = title if title else apply_url
    t = normalize_title(text).lower()
    if any(re.search(r'\b' + tok + r'\b', t) for tok in CSUITE_TOKENS):
        return 'C-Suite', 3.0
    if has_phrase(t, 'vp') or has_phrase(t, 'svp') or has_phrase(t, 'evp'):
        return 'VP', 2.0
    if has_phrase(t, 'director') or has_phrase(t, 'head of'):
        return 'Director', 1.5
    if has_phrase(t, 'manager'):
        return 'Manager', 0.5
    return 'IC', 0.0

def company_size_fit(n):
    if n is None:
        return 0.0
    if n < 100:
        return 1.0
    if n <= 500:
        return 2.0
    if n <= 1000:
        return 1.5
    return 0.0

def industry_fit(industries_text):
    t = industries_text.lower()
    if any_phrase(t, ['fintech','financial technology']):
        return 2.0
    if any_phrase(t, ['dtc','direct to consumer','direct-to-consumer']):
        return 2.0
    if any_phrase(t, ['saas','software','ecommerce','e-commerce','it','marketing']):
        return 1.0
    return 0.0

def location_fit(loc_text):
    t = (loc_text or '').lower()
    if any_phrase(t, LOCATION_STATES):
        return 1.0
    if any(city in t for city in LOCATION_CITY_FALLBACKS):
        return 1.0
    return 0.0

def margin_proxy(industries_text):
    t = industries_text.lower()
    if any_phrase(t, ['saas','software']) or any_phrase(t, ['fintech','financial technology']):
        return 2.0
    if any_phrase(t, ['marketing','services']):
        return 1.0
    return 0.0

def funding_amount_score(amt):
    if amt is None:
        return 0.0
    if amt > 100_000_000:
        return 1.5
    if amt >= 10_000_000:
        return 1.0
    if amt >= 1_000_000:
        return 0.5
    if amt > 0:
        return 0.25
    return 0.0

def funding_recency_score(year):
    if year is None:
        return 0.0
    if year >= 2024:
        return 0.5
    if year >= 2022:
        return 0.25
    return 0.0

def funding_type_score(ftype, flags):
    if not ftype:
        return 0.0
    t = ftype.lower()
    if any(k in t for k in ['series a','series b','series c','series d','growth','private equity']):
        return 0.5
    if any(k in t for k in ['corporate','venture','seed']):
        return 0.25
    if any(k in t for k in ['grant','debt','angel']):
        return 0.0
    flags.append(f'ambiguous funding type "{ftype}" not in defined tiers - scored 0, review')
    return 0.0

def flatten(r):
    v5 = r.get('v5_processed_job_data') or {}
    ecd = r.get('enriched_company_data') or {}
    job_id = r.get('id') or r.get('apply_url')
    return {
        'job_id': job_id,
        'apply_url': r.get('apply_url') or '',
        'title': v5.get('core_job_title') or '',
        'job_category': v5.get('job_category') or '',
        'workplace_type': v5.get('workplace_type') or '',
        'workplace_location': v5.get('formatted_workplace_location') or '',
        'company_name': ecd.get('name') or v5.get('company_name') or '',
        'website': ecd.get('homepage_uri') or v5.get('company_website') or '',
        'industries': ecd.get('industries') or [],
        'nb_employees': ecd.get('nb_employees'),
        'hq_country': ecd.get('hq_country') or '',
        'activities': ecd.get('activities') or [],
        'latest_funding_amount': ecd.get('latest_funding_amount'),
        'latest_funding_year': ecd.get('latest_funding_year'),
        'latest_funding_type': ecd.get('latest_funding_type'),
        'organization_type': ecd.get('organization_type') or '',
    }

def is_sales_title(f):
    return has_phrase(f['job_category'], 'sales') or has_phrase(f['title'], 'sales')

def is_nonprofit_or_gov(f):
    return f['organization_type'] in ('Non-Profit', 'Government')

def score_row(f):
    flags = []
    ded = 0.0
    override_ignore = False
    override_reasons = []

    title = f['title']
    industries_text = joined(f['industries'])
    activities_text = joined(f['activities'])
    org_type = f['organization_type']
    company_name = f['company_name']
    nb_emp = f['nb_employees']

    niche, niche_ded, niche_flags = niche_classify(f)
    ded += niche_ded
    flags.extend(niche_flags)

    if is_blue_collar(f):
        override_ignore = True
        override_reasons.append('blue-collar/production-floor role - out of scope')
        niche = 'None'

    if any(cn in company_name.lower() for cn in CURRENT_CLIENTS):
        override_ignore = True
        override_reasons.append('current client - excluded')

    if nb_emp is not None and nb_emp > 1000:
        override_ignore = True
        override_reasons.append(f'nb_employees={nb_emp} > 1000 - auto-Ignore')

    if niche == 'Ops & Supply Chain':
        override_ignore = True
        override_reasons.append('Ops & Supply Chain niche - hands-off/out of active scope')

    if any_phrase(industries_text, RELIGIOUS_SIGNALS) or any_phrase(activities_text, RELIGIOUS_SIGNALS) \
       or any_phrase(company_name, RELIGIOUS_SIGNALS):
        override_ignore = True
        override_reasons.append('religious organization - hard pass')

    sports_hit = any_phrase(industries_text, SPORTS_HOSP_INDUSTRY)
    if not sports_hit and not industries_text and not activities_text:
        sports_hit = any_phrase(company_name, SPORTS_HOSP_NAME_FALLBACK)
    if not sports_hit:
        sports_hit = any_phrase(activities_text, SPORTS_HOSP_ACTIVITY_PHRASES)
    if sports_hit:
        if is_tech_vendor(industries_text, activities_text):
            flags.append('sports/hospitality signal but looks like tech vendor to that industry - not excluded, review')
        else:
            override_ignore = True
            override_reasons.append('sports/hospitality organization - hard pass')

    verdict, how = category_override(company_name, industries_text, activities_text,
                                     RESTAURANT_INDUSTRY, RESTAURANT_OPERATOR_ACTIVITY, RESTAURANT_NAME)
    if verdict == 'exclude':
        override_ignore = True
        override_reasons.append(f'restaurant/food-service operator ({how}) - hard pass')
    elif verdict == 'vendor':
        flags.append('restaurant signal but looks like tech vendor to restaurants - not excluded, review')

    verdict, how = category_override(company_name, industries_text, activities_text,
                                     SENIOR_CARE_INDUSTRY, SENIOR_CARE_OPERATOR_ACTIVITY, SENIOR_CARE_NAME)
    if verdict == 'exclude':
        override_ignore = True
        override_reasons.append(f'senior care/assisted living/home care ({how}) - hard pass')
    elif verdict == 'vendor':
        flags.append('senior-care signal but looks like tech vendor to that industry - not excluded, review')

    if any_phrase(industries_text, FRANCHISE_SIGNALS) or any_phrase(activities_text, FRANCHISE_SIGNALS) \
       or any_phrase(company_name, FRANCHISE_SIGNALS):
        if any_phrase(activities_text, FRANCHISE_CORP_EXCEPTION_PHRASES):
            flags.append('franchise/MLM signal but corporate-side franchisor language present - not excluded, review')
        else:
            override_ignore = True
            override_reasons.append('franchise/MLM operator structure - hard pass')

    if any_phrase(industries_text, PR_SIGNALS) or any_phrase(activities_text, PR_SIGNALS):
        div_hits = sum(1 for s in PR_DIVERSIFICATION_SIGNALS if has_phrase(activities_text, s))
        if div_hits >= 2 and nb_emp is not None and nb_emp <= 1000:
            flags.append('PR firm but diversified beyond core PR - not excluded, general-agency deduction applies, review')
            ded -= 1.5
            flags.append('general agency deduction: -1.5')
        else:
            override_ignore = True
            override_reasons.append('PR firm/agency - hard pass')

    caredelivery_hit = any_phrase(industries_text, HEALTHCARE_CAREDELIVERY_SIGNALS) or any_phrase(activities_text, HEALTHCARE_CAREDELIVERY_SIGNALS)
    device_hit = any_phrase(industries_text, MEDICAL_DEVICE_SIGNALS) or any_phrase(activities_text, MEDICAL_DEVICE_SIGNALS)
    if caredelivery_hit:
        if is_tech_vendor(industries_text, activities_text):
            flags.append('healthcare signal but looks like tech vendor to that industry - not excluded, review')
        else:
            override_ignore = True
            override_reasons.append('healthcare services/care-delivery organization - hard pass')
    elif device_hit:
        flags.append('medical device/biotech manufacturer signal (not care-delivery) - not excluded, flagged for manual confirm-in-scope')

    if any_phrase(industries_text, CLINICAL_RESEARCH_SIGNALS) or any_phrase(activities_text, CLINICAL_RESEARCH_SIGNALS):
        override_ignore = True
        override_reasons.append('clinical research organization - hard pass')

    if any_phrase(industries_text, PHARMA_SIGNALS) or any_phrase(activities_text, PHARMA_SIGNALS):
        override_ignore = True
        override_reasons.append('pharmaceutical company - hard pass')

    if any_phrase(industries_text, EDUCATION_SIGNALS) or any_phrase(activities_text, EDUCATION_SIGNALS):
        override_ignore = True
        override_reasons.append('educational institution - hard pass')

    bank_check_text = re.sub(r'investment banking', '', industries_text, flags=re.IGNORECASE)
    bank_hit = any_phrase(bank_check_text, ['banking']) or any_phrase(activities_text, TRADITIONAL_BANK_ACTIVITY_SIGNALS) \
               or any_phrase(company_name, TRADITIONAL_BANK_NAME_SIGNALS)
    if bank_hit and not any_phrase(industries_text, ['fintech','financial technology']) \
       and not is_tech_vendor(industries_text, activities_text):
        override_ignore = True
        override_reasons.append('traditional/community bank - hard pass (brick-and-mortar retail banking, distinct from fintech)')

    if not any('PR firm' in r or 'general-agency' in r for r in override_reasons + flags):
        if any_phrase(industries_text, GENERAL_AGENCY_SIGNALS) or any_phrase(activities_text, GENERAL_AGENCY_SIGNALS):
            ded -= 1.5
            flags.append('general agency signal: -1.5 (revenue not available in data - flagged, not excluded)')

    if nb_emp is not None and nb_emp < 50:
        if any_phrase(industries_text, DEV_SHOP_SIGNALS) or any_phrase(activities_text, DEV_SHOP_SIGNALS):
            ded -= 1.5
            flags.append('small software dev shop, <50 employees: -1.5')

    if f['hq_country'] and f['hq_country'] != 'US':
        flags.append(f'non-US HQ ({f["hq_country"]}) - review, not penalized')

    if title.strip().lower() in ('director', 'vice president', 'vp'):
        flags.append('ambiguous bare senior title, no functional descriptor - flag for manual research')

    seniority, sen_score = seniority_fit(title, f['apply_url'])

    size_score = company_size_fit(nb_emp)
    ind_score = industry_fit(industries_text)
    loc_score = location_fit(f['workplace_location'])
    margin_score = margin_proxy(industries_text)
    famt_score = funding_amount_score(f['latest_funding_amount'])
    frec_score = funding_recency_score(f['latest_funding_year'])
    ftype_score = funding_type_score(f['latest_funding_type'], flags)
    niche_fit_score = 2.0 if niche != 'None' else 0.0

    base_score = (size_score + ind_score + loc_score + margin_score + famt_score +
                  frec_score + ftype_score + niche_fit_score + sen_score)
    final_score = round(base_score + ded, 2)

    if override_ignore:
        priority = 'Ignore'
    elif final_score >= 10:
        priority = 'ICP'
    elif final_score >= 7:
        priority = 'Qualified'
    elif final_score >= 4:
        priority = 'Marginal'
    else:
        priority = 'Ignore'

    if f.get('_sales_flag'):
        flags.append('sales title - kept (sales-title removal paused), would previously have been auto-excluded')
    all_flags = override_reasons + flags

    return {
        'job_id': f['job_id'],
        'company_name': company_name,
        'title': title,
        'seniority': seniority,
        'apply_url': f['apply_url'],
        'job_category': f['job_category'],
        'niche': niche,
        'workplace_type': f['workplace_type'],
        'workplace_location': f['workplace_location'],
        'website': f['website'],
        'industries': industries_text,
        'nb_employees': nb_emp if nb_emp is not None else '',
        'hq_country': f['hq_country'],
        'activities': activities_text,
        'latest_funding_amount': f['latest_funding_amount'] if f['latest_funding_amount'] is not None else '',
        'latest_funding_year': f['latest_funding_year'] if f['latest_funding_year'] is not None else '',
        'latest_funding_type': f['latest_funding_type'] or '',
        'organization_type': org_type,
        'score': final_score,
        'priority': priority,
        'flags': ' ; '.join(all_flags),
    }

def run(input_files, output_path):
    raw = []
    for path in input_files:
        with open(path) as fh:
            raw.extend(json.load(fh))
    print(f'raw total: {len(raw)}')

    flat = [flatten(r) for r in raw]

    seen = set()
    d1 = []
    for f in flat:
        if f['job_id'] in seen:
            continue
        seen.add(f['job_id'])
        d1.append(f)
    print(f'after dedup pass 1 (job_id): {len(d1)}')

    removed_sales = 0
    removed_orgtype = 0
    kept = []
    for f in d1:
        if is_sales_title(f):
            if REMOVE_SALES_TITLES:
                removed_sales += 1
                continue
            f['_sales_flag'] = True
        if is_nonprofit_or_gov(f):
            removed_orgtype += 1
            continue
        kept.append(f)
    print(f'removed (sales title): {removed_sales}' + ('' if REMOVE_SALES_TITLES else ' (sales removal paused - sales titles kept and flagged)'))
    print(f'removed (non-profit/government org_type): {removed_orgtype}')
    print(f'remaining after removal filters: {len(kept)}')

    results = [score_row(f) for f in kept]

    seen2 = set()
    final_rows = []
    for r in results:
        key = (r['company_name'].strip().lower(), r['title'].strip().lower(), r['workplace_location'].strip().lower())
        if key in seen2:
            continue
        seen2.add(key)
        final_rows.append(r)
    print(f'rows after dedup pass 2: {len(final_rows)}')

    final_rows.sort(key=lambda r: (-r['score'], r['company_name'].lower()))

    print('priority breakdown:', collections.Counter(r['priority'] for r in final_rows))
    print('niche breakdown:', collections.Counter(r['niche'] for r in final_rows))

    cols = ['job_id','company_name','title','seniority','apply_url','job_category','niche',
            'workplace_type','workplace_location','website','industries','nb_employees',
            'hq_country','activities','latest_funding_amount','latest_funding_year',
            'latest_funding_type','organization_type','score','priority','flags']

    with open(output_path, 'w', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for r in final_rows:
            writer.writerow(r)
    print(f'CSV written to {output_path}')

    summary = {
        'raw_total': len(raw),
        'after_dedup_1': len(d1),
        'removed_sales': removed_sales,
        'sales_removal_paused': not REMOVE_SALES_TITLES,
        'removed_nonprofit_gov': removed_orgtype,
        'final_rows': len(final_rows),
        'priority': dict(collections.Counter(r['priority'] for r in final_rows)),
        'niche': dict(collections.Counter(r['niche'] for r in final_rows)),
        'top': [{k: r[k] for k in ('company_name','title','niche','score','priority','workplace_location','apply_url')}
                for r in final_rows if r['priority'] in ('ICP','Qualified')][:25],
        'niche_none_qualified_plus': sum(1 for r in final_rows
                                         if r['priority'] in ('ICP','Qualified') and r['niche'] == 'None'),
        'excluded_restaurant': sum(1 for r in final_rows if 'restaurant/food-service operator' in r['flags']),
        'excluded_senior_care': sum(1 for r in final_rows if 'senior care/assisted living' in r['flags']),
    }
    summary_path = os.environ.get('SUMMARY_JSON')
    if summary_path:
        with open(summary_path, 'w') as fh:
            json.dump(summary, fh, indent=2, default=str)
    return summary

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    output_path = sys.argv[1]
    input_files = sys.argv[2:]
    run(input_files, output_path)
