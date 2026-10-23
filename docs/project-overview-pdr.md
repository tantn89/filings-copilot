# Project overview

## Intent

Answer research questions about US public companies from their SEC 10-K filings, the way
a careful analyst would. Every number must come from audited structured data, every
explanation must be cited to the filer's own words, and the assistant must say "not in
the data" rather than guess. In finance a confident wrong number is worse than no answer,
so correctness and traceability beat coverage and fluency.

## Users

- **Researchers and analysts** asking "how much" and "why" questions about a company or a
  comparison across companies.
- **Engineers evaluating the project** (it is also a portfolio piece). They need to verify
  the claims quickly, so the live demo shows the evidence trace (the exact SQL, the
  retrieved passages) and the README links reproducible evals.

## Product rules

These rules are why the code looks the way it does. Changing one is a product decision.

1. **Numbers are computed, never generated.** Figures come from `query_financials` over
   the normalised XBRL table; derived figures (margins, growth) should be computed in SQL.
   The system prompt in `src/filings_copilot/research_agent.py` (`SYSTEM_PROMPT`) states
   this to the model; the eval grades numbers against XBRL.
2. **Narrative claims carry citations** to passage ids that the agent actually retrieved.
   The UI marks any id the agent did not retrieve as unverified.
3. **Abstain plainly.** Forecasts, companies not ingested, and facts absent from the
   filings get a refusal that states no invented figure. Unanswerable golden cases carry
   forbidden patterns, so a refusal that still states a dollar amount fails.
4. **The public demo must have a bounded cost.** Every question spends LLM credits.

## Non-goals

- Investment advice, forecasts, or price data. Only what the 10-K and its XBRL say.
- Real-time or quarterly (10-Q, 8-K) data. Annual 10-K only.
- Broad coverage. The demo ships three companies (NVDA, MSFT, AAPL) for two fiscal years;
  scaling to more tickers is on the roadmap, not a current promise.
- User accounts, saved history, or multi-tenant serving.

## Domain terms

| Term | Meaning here |
|---|---|
| 10-K | A company's annual report filed with the SEC |
| Item 1 / 1A / 7 / 7A | 10-K sections: Business, Risk Factors, MD&A, Market Risk. Only these are ingested |
| XBRL companyfacts | SEC's structured, audited numbers per company, keyed by accounting concept |
| Concept | An XBRL tag such as `RevenueFromContractWithCustomerExcludingAssessedTax`; several concepts can mean one metric |
| Metric | Our normalised name (`revenue`, `gross_profit`, ...), chosen from concepts by priority in `financial_facts_loader.py` |
| Fiscal year (FY) | Named by `fiscal_calendar.fiscal_year`: the year of the period end minus seven days, so 52/53-week years ending in early January get the right label |
| Accession number | SEC's unique id of one filing; filings and chunks are keyed by it |
| Passage / chunk id | `TICKER-FY-ITEM-NNNN`, the id the model cites in brackets |

## Hard constraints

- **SEC fair-access policy.** Requests must send a descriptive User-Agent with a contact
  address (`FILINGS_SEC_USER_AGENT`) and stay under the SEC rate limit. See
  `edgar_client.py`.
- **Model output is untrusted input.** Tool arguments, SQL and citations from the model
  are validated before use, on both the API and the UI side.
- **Public demo spend.** In-process question caps are a courtesy limit; the hard bound is
  the provider-side spend limit (see [deployment-guide.md](deployment-guide.md)).
