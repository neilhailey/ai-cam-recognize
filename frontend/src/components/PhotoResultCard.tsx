import type { PhotoResult } from '../types'

const EMOJI: Record<string, string> = {
  '3-axis': '✅',
  '4-axis': '🔄',
  '5-axis': '⚠️',
  uncertain: '❓',
}

export function PhotoResultCard({ result, imageUrl }: { result: PhotoResult; imageUrl: string }) {
  return (
    <div className="photo-result">
      <div className="photo-result__grid">
        <img className="photo-result__img" src={imageUrl} alt="uploaded" />
        <div>
          <div className="photo-result__verdict">
            <span className="photo-result__emoji">{EMOJI[result.verdict] ?? '❓'}</span>
            <span>{result.verdict === 'uncertain' ? 'Uncertain' : `Likely ${result.verdict}`}</span>
            {result.available && (
              <span className="photo-result__conf">{Math.round(result.confidence * 100)}% confidence</span>
            )}
          </div>
          <p className="photo-result__reasoning">{result.reasoning}</p>
          {result.suspected_undercuts.length > 0 && (
            <div className="photo-result__undercuts">
              <strong>Possible undercuts:</strong>
              <ul>{result.suspected_undercuts.map((u, i) => <li key={i}>{u}</li>)}</ul>
            </div>
          )}
        </div>
      </div>
      <div className="callout">
        {result.caveat ??
          'A single photo cannot prove undercuts. Upload the STL model for a definitive, geometry-based verdict.'}
      </div>
    </div>
  )
}
