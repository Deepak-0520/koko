# KOKO (ここ) — working name

**Every address in Japan, explained: today's risk and long-term risk, in your language.**

An AI-integrated WebGIS dashboard that answers two questions for anyone living in Japan:

1. **"Is this place safe to live in?"** (a one-time decision: renting, buying, relocating staff)
2. **"Is my place OK today?"** (the daily habit: live warnings, heat, rain, and earthquakes for my saved places)

Research date: 2026-10-19. Facts and sources are at the bottom.

---

## 1. Why this idea (and why not the others)

### What the research found

| Signal | What it means for us |
|---|---|
| **4.28 million foreign residents (June 2026, record)**, passing 4M for the first time at end-2025 | A large, growing user base that is underserved by Japanese-only tools |
| **JMA changed the warning system on 28 May 2026**: new Level 1–5 for heavy rain, river flood, landslide, and storm surge; new "Level 4 Danger Warning (危険警報)"; flood warnings abolished and replaced by ~400 river-specific warnings | People are confused. JMA's English pages give only basic explanations. **This is a timely gap.** |
| **MLIT Real Estate Information Library (不動産情報ライブラリ) API** added flood, landslide, tsunami, storm surge, and shelters (Nov 2025) and disaster history (Dec 2025); **inland/pluvial flood (内水) data comes in FY2026** | Address-level hazard data is now available by API. Few products have caught up yet. |
| **PLATEAU building models carry per-building flood attributes** (depth, rank, duration) plus height and storeys, in 250+ cities, growing toward ~500 | We can do *building-level* risk ("Is my 3rd floor above the expected flood depth?"), which almost nobody shows to end users. |
| **Fire insurance flood premiums have been tiered in 5 levels by municipality since Oct 2024** (avg +13%) | Real money is tied to location risk, so homeowners and renters care. |
| Real-estate agents must explain flood hazard maps in the pre-contract explanation (重要事項説明) | A B2B customer with a legal obligation. |
| Existing tools: **Safety Tips** (official, multilingual push alerts, no address analysis), **NERV / Yahoo!防災** (excellent, mostly Japanese), **Hazard Map Portal** (maps, not explanations), **municipal hazard PDFs** (fragmented, often Japanese-only) | **The gap:** nobody joins *address-level static risk + building-level detail + live alerts + plain-language AI explanation in the user's language* in one place. |

### Ideas I checked and rejected as the core product

| Idea | Verdict |
|---|---|
| **Bear sighting map** (2025 was a record year) | Crowded: FASTBEAR, 熊出没マップ (144k records), KumaWatch already exist. Use it as a **layer** instead. |
| **Akiya (vacant house) finder** | Crowded: akiyajapan.com claims 945k+ listings. Not a geospatial-engineering showcase. |
| **Sewer and sinkhole risk via InSAR** (after the Yashio collapse) | Strong GeoAI showcase, but B2G only, not daily use, and ground-truth data is limited. Kept as **Phase 4 stretch module**. |
| **Spatial ID (空間ID) platform** | Government and enterprise driven (Guideline v1.2 published June 2026). We **align with it** (index our cells with Spatial ID) as a signal to Japanese employers instead of building it. |

### Why it fits *you* specifically

It uses all three of your skills in one product:
- **Geospatial engineering:** data pipelines, PostGIS, CityGML → 3D Tiles/vector tiles, spatial joins
- **WebGIS:** MapLibre with GSI tiles, PLATEAU 3D, a fast responsive dashboard
- **GeoAI:** an LLM agent grounded in tool calls, plus an ML model and (stretch) InSAR analysis

It also uses exactly the national datasets (PLATEAU, GSI, MLIT, JMA, NIED) that Japanese geospatial companies work with every day. Interviewers will recognize them immediately.

---

## 2. The product

### Users
1. **Foreign residents and newcomers** (primary, free): choosing an apartment, daily safety
2. **Relocation and HR firms** that bring in foreign staff (B2B): send a risk report for each candidate apartment
3. **Real-estate agents** (B2B): a bilingual hazard explanation sheet for 重要事項説明
4. **Japanese users** too: the AI explanation works in Japanese, and NERV and Yahoo don't do address reports

### Core features

**A. Address Report (the "decision" feature)**
Type an address or drop a pin, and get:
- River flood depth (max scale), inland flood (when released), landslide zones, tsunami, storm surge: *Reinfolib API / 国土数値情報*
- **Building-level:** this building's expected flood depth vs. its storeys → "vertical evacuation feasible above floor N": *PLATEAU*
- Earthquake: 30-year probability of shaking at 6-lower or higher: *J-SHIS*
- Past disasters nearby: *Reinfolib disaster history*
- Nearest designated evacuation sites, with walking route and time: *Reinfolib shelters + routing*
- Zoning, land price trend: *Reinfolib*
- Insurance flood tier for the municipality (when the tier list is obtainable)
- **AI summary** in EN / JA / ZH / VI / NE / KO / ES… where every number cites its source layer
- Export: a bilingual PDF (Japanese + user's language) the user can show to the real-estate agent

**B. My Places (the "daily" feature: retention)**
- Save home, work, and a child's school
- Live status per place: JMA warnings in the **new Level 1–5 system** explained plainly, heat stroke alert (WBGT), rain nowcast, earthquake and tsunami, optional nearby bear sightings
- **AI morning brief**: "Today at Home: Level 2 heavy-rain advisory from 15:00. Your area is not in a landslide zone. Bring an umbrella. Heat index is high, so avoid outdoor work 12–15h."
- Push/email when a saved place reaches Level 3+

**C. Ask the Map (the "AI-integrated" showcase)**
A chat panel where the LLM *uses tools* rather than guessing:
`geocode()`, `get_hazards(lat,lon)`, `get_building(lat,lon)`, `get_warnings(area)`, `find_shelters()`, `compare_addresses()`
- "Compare these 3 apartments for flood and earthquake risk" → table plus map highlight
- "What does 'Level 4 危険警報' mean for me right now?"
- Guardrails: no answer without a tool result; always shows sources; links to official JMA for emergencies; never replaces official evacuation orders.

---

## 3. Technical architecture

```
Frontend:   React + TypeScript + MapLibre GL JS (GSI vector tiles / 地理院ベクトルタイル)
            deck.gl or CesiumJS for PLATEAU 3D Tiles view
            i18n (react-i18next), PWA for mobile "add to home screen"
Backend:    Python FastAPI
            PostGIS (hazard polygons, shelters, PLATEAU building footprints + attributes)
            Redis cache (warnings refresh every few minutes)
            Scheduled ingesters: JMA (防災情報XML official feed), Reinfolib API, heat alert
AI:         Claude API with tool use (agent loop); prompt caching; structured outputs
            Every answer is built only from tool results and carries source attribution
Data prep:  PLATEAU CityGML → building table (flood depth, storeys, height) via
            plateau-gis-converter / custom lxml parser → PostGIS + PMTiles
Index:      H3 or Spatial ID (空間ID) cells for fast point lookups
Hosting:    Frontend on Cloudflare Pages/Vercel; API on Fly.io/Render; DB on Supabase (PostGIS)
```

**Data caveats to resolve early:**
- JMA's `bosai/*.json` endpoints are **undocumented**. Use them for prototyping only, and the official 防災情報XML feed for production.
- The Reinfolib API needs an **API key application**. Apply in week 1.
- GSI tiles, J-SHIS, and PLATEAU each have **attribution / licence terms**. Read them before any commercial use.
- PLATEAU coverage is ~250 cities, so the app must degrade gracefully to polygon-level hazard where building data doesn't exist.

---

## 4. Roadmap (about 14 weeks, part-time)

| Phase | Weeks | Deliverable |
|---|---|---|
| **0. Setup** | 1 | Apply for Reinfolib API key; repo; read licences; pick MVP area: **Tokyo 23 wards + 1 regional city** (e.g. Kumamoto or Shizuoka) to prove it generalizes |
| **1. Data pipeline** | 2–4 | PostGIS loaded: flood/landslide/tsunami/storm surge polygons, shelters, PLATEAU buildings with flood attributes, J-SHIS mesh. Point-lookup API `/report?lat&lon` returning in under 300 ms |
| **2. WebGIS MVP** | 5–7 | Map + address search + Address Report panel + 3D building view + bilingual PDF export. **Deploy publicly.** |
| **3. AI layer** | 8–10 | Ask-the-Map agent with tools; multilingual summaries; evaluation set of ~50 questions to check that it never states a number without a source |
| **4. Daily features** | 11–12 | My Places, live JMA warnings (new Level system), heat alert, morning brief, notifications |
| **5. Polish & launch** | 13–14 | Japanese UI review, demo video (JA + EN), write-ups, Show HN / Reddit r/japanlife / X |
| **Stretch** | later | InSAR ground-deformation layer from Sentinel-1 (GeoAI depth); ML model for flood-damage susceptibility; bear-sighting layer; B2B PDF branding for agents |

---

## 5. The career side (this matters as much as the code)

- **Japanese:** target JLPT N3 → N2. Even N3 plus a Japanese UI you built yourself shows commitment. Most job posts list Japanese ability.
- **Write in Japanese:** publish 4–6 technical articles on **Zenn or Qiita** (e.g. "PLATEAUの建物浸水属性で垂直避難判定をしてみた", "2026年の新しい警戒レベルをLLMで多言語解説する"). Japanese engineers and recruiters read these.
- **Open-source pieces:** release the PLATEAU flood-attribute extractor and the JMA warning parser as small libraries. Contributions to MapLibre, QGIS plugins, or Project-PLATEAU repos get noticed.
- **Events:** submit to **FOSS4G Japan**, the **PLATEAU AWARD / PLATEAU Hackathons**, and **Geospatial / 地理空間情報 events (G空間EXPO)**. Check the 2026–27 dates on their sites.
- **Target companies** (fit with this project): Eukarya (Re:Earth / PLATEAU VIEW), MIERUNE, Pasco, Kokusai Kogyo, Asia Air Survey, Zenrin, Esri Japan, Weathernews, Spectee (AI disaster info), Gehirn (NERV), Synspective (SAR + AI), Tellus.
- **Visa path:** most engineers enter on 技術・人文知識・国際業務 (516k holders as of June 2026). A degree in a related field plus a job offer is the standard route.
- **Pitch line for interviews:** *"I built a multilingual address-risk platform on PLATEAU, Reinfolib, and JMA data with a grounded LLM agent. N thousand people use it monthly."*

---

## 6. Success metrics

- Address report under 2 s; correct source on 100% of numbers in the AI eval set
- 1,000 monthly active users within 3 months of launch (reachable via foreign-resident communities)
- At least 1 relocation or real-estate company piloting the PDF report
- At least 3 Japanese articles, 1 conference talk or hackathon entry, and recruiter conversations started

---

## Sources

- In-Japan foreign residents 4.28M (June 2026): [Kumanichi](https://kumanichi.com/articles/2047152), [MOJ](https://www.moj.go.jp/isa/publications/press/13_00040.html)
- JMA warning overhaul 28 May 2026: [YOLO JAPAN](https://media.yolo-japan.com/en/1988/)
- Reinfolib API hazard additions: [MLIT press PDF](https://www.mlit.go.jp/report/press/content/001969981.pdf), [MLIT press](https://www.mlit.go.jp/report/press/tochi_fudousan_kensetsugyo17_hh_000001_00070.html)
- PLATEAU flood attribute spec: [uro:RiverFloodingRiskAttribute](https://www.mlit.go.jp/plateaudocument01-02/contents/1/uro_RiverFloodingRiskAttribute.html); Vision 2026: [MLIT PLATEAU](https://www.mlit.go.jp/en/toshi/daisei/plateau_en_2.html); [Project-PLATEAU GitHub](https://github.com/Project-PLATEAU)
- Spatial ID API research gap: [ISPRS 2025](https://isprs-archives.copernicus.org/articles/XLVIII-4-W15-2025/25/2025/isprs-archives-XLVIII-4-W15-2025-25-2025.pdf); Guideline v1.2: [IPA](https://www.ipa.go.jp/pressrelease/2026/press20260604.html)
- Multilingual disaster info gaps: [JapanLifeStart](https://japanlifestart.com/en/essentials/disaster-evacuation-apps-japan), [LO-PAL](https://lo-pal.app/guide/en/disaster-alerts-english-apps-maps-shelters), [Shinjuku Safety Tips](https://www.foreign.city.shinjuku.lg.jp/en/kurashi/safetytips/)
- Fire insurance flood tiering: [NLI Research](https://www.nli-research.co.jp/files/topics/75365_ext_18_0.pdf?site=nli), [FSA](https://www.fsa.go.jp/singi/suisai/gaiyou.pdf)
- Bear data fragmentation: [FASTBEAR PR](https://prtimes.jp/main/html/rd/p/000000002.000175363.html), [KumaWatch](https://kuma-watch.jp/articles/bear-2025-retrospective)
- Akiya market: [akiyajapan guide](https://www.akiyajapan.com/articles/every-akiya-bank-japan-complete-english-guide?lang=en)
- Sewer inspection after Yashio: [MLIT](https://www.mlit.go.jp/report/press/mizukokudo13_hh_000676.html), [BUILT](https://built.itmedia.co.jp/bt/articles/2511/12/news120.html)
- JMA JSON (unofficial usage): [condition_aggregator_jma](https://pub.dev/packages/condition_aggregator_jma)
- J-SHIS: [NIED repo](https://nied-repo.bosai.go.jp/record/1859/files/3903_22_1.pdf)
- Japan geospatial companies: [JEO roundup Mar 2026](https://www.japanearthobserver.com/jeo-12-news-roundup-14-mar-2026/), [Synspective](https://www.synspective.com/), [MIERUNE / OSGeo](https://www.osgeo.org/service-providers/mierune-inc/)
- GIS jobs and skills: [BootcampGIS 2026 report](https://bootcampgis.com/gis-jobs-report/), [Glassdoor Japan GIS](https://www.glassdoor.com/Job/japan-gis-jobs-SRCH_IL.0,5_IN123_KO6,9.htm)
- LLM × GIS in Japan: [FOSS4G 2026 talk](https://talks.osgeo.org/foss4g-2026/speaker/UJWWE8/)
