import './Research.css'

export type ResearchCandidate = {
  candidate_id: string
  brand: string | null
  model: string | null
  product_name: string | null
  product_url: string | null
  matching_features: string[]
  differing_features: string[]
  missing_verification: string[] | string
  price_as_displayed: string | null
  currency: string | null
  status: 'unverified_possible_match'
}

function safeProductUrl(value: string | null) {
  if (!value) return null
  try {
    const url = new URL(value)
    return ['http:', 'https:'].includes(url.protocol) ? url.href : null
  } catch {
    return null
  }
}

function FeatureList({ title, values }: {
  title: string
  values: string[]
}) {
  if (!values.length) return null
  return (
    <details className="research-features">
      <summary>{title} ({values.length})</summary>
      <ul>{values.map((value, index) => <li key={index}>{value}</li>)}</ul>
    </details>
  )
}

export default function ResearchCandidates({
  candidates, limitations = [],
}: {
  candidates: ResearchCandidate[]
  limitations?: string[]
}) {
  return (
    <section className="research-results" aria-label="Possible product matches">
      <p className="helper">
        Research leads only. These do not confirm your item's identity.
        Listing prices are not purchase prices or replacement valuations.
      </p>

      {candidates.length === 0 && (
        <p>No defensible product matches were returned.</p>
      )}

      <div className="research-candidate-list">
        {candidates.map(candidate => {
          const url = safeProductUrl(candidate.product_url)
          const unknowns = Array.isArray(candidate.missing_verification)
            ? candidate.missing_verification
            : candidate.missing_verification
              ? [candidate.missing_verification] : []

          return (
            <article className="research-candidate" key={candidate.candidate_id}>
              <span className="research-badge">Unverified suggestion</span>
              <h4>
                {candidate.product_name ||
                  [candidate.brand, candidate.model].filter(Boolean).join(' · ') ||
                  'Possible product match'}
              </h4>
              <p>
                Brand: {candidate.brand ?? 'Unknown'}
                {' · '}Model: {candidate.model ?? 'Unknown'}
              </p>
              <p>
                Listing price: {candidate.price_as_displayed ?? 'Not available'}
                {candidate.currency ? ` · ${candidate.currency}` : ''}
              </p>
              {url && (
                <a href={url} target="_blank" rel="noopener noreferrer">
                  View product source ↗
                </a>
              )}

              <FeatureList title="Matching features"
                values={candidate.matching_features ?? []} />
              <FeatureList title="Reported differences"
                values={candidate.differing_features ?? []} />
              <FeatureList title="Still needs verification" values={unknowns} />
            </article>
          )
        })}
      </div>

      <FeatureList title="Research limitations" values={limitations} />
    </section>
  )
}
