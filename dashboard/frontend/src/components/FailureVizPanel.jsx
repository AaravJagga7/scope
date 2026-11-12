import React from 'react';

export default function FailureVizPanel({ failureData }) {
  const strategies = failureData?.strategies || ['Fixed', 'HPA', 'QueueBacklog', 'SCOPE-X'];
  const profiles = failureData?.profiles || ['Flash Sale', 'Ramp', 'Viral Product', 'Slow Payment Api'];
  const matrix = failureData?.heatmap_matrix || [
    [70.5, 49.7, 49.9, 79.2],
    [67.2, 38.4, 42.4, 79.2],
    [67.2, 42.5, 44.8, 79.2],
    [0.7, 1.0, 0.7, 0.0]
  ];

  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: '1.8fr 1.2fr', gap: '20px', marginBottom: '24px' }}>
        
        {/* Heatmap Card */}
        <div className="jobgio-card">
          <h3 style={{ fontSize: '18px', fontWeight: 700, color: '#111111', letterSpacing: '-0.4px', marginBottom: '4px' }}>
            SLO Failure Matrix (% Violations)
          </h3>
          <p style={{ fontSize: '12px', color: '#8C8E9B', marginBottom: '20px' }}>
            Percentage of experiment duration exceeding p95 &gt; 500ms or error &gt; 1%
          </p>

          <div style={{ overflowX: 'auto' }}>
            <table className="clean-table">
              <thead>
                <tr>
                  <th>Strategy</th>
                  {profiles.map((p, i) => <th key={i}>{p}</th>)}
                </tr>
              </thead>
              <tbody>
                {strategies.map((strat, sIdx) => (
                  <tr key={sIdx}>
                    <td style={{ fontWeight: 700, color: strat === 'SCOPE-X' ? '#1B65F1' : '#111' }}>
                      {strat}
                    </td>
                    {matrix[sIdx]?.map((val, pIdx) => {
                      const isSafe = val < 5.0;
                      const isHigh = val > 50.0;
                      return (
                        <td key={pIdx}>
                          <span className={`pill-badge ${isSafe ? 'badge-active' : isHigh ? 'badge-danger' : 'badge-warning'}`}>
                            {val}%
                          </span>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Recovery Time Summary */}
        <div className="jobgio-card">
          <h3 style={{ fontSize: '18px', fontWeight: 700, color: '#111111', letterSpacing: '-0.4px', marginBottom: '4px' }}>
            SLO Recovery Dynamics
          </h3>
          <p style={{ fontSize: '12px', color: '#8C8E9B', marginBottom: '20px' }}>
            Mean seconds required to restore compliance following traffic burst
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
            {[
              { strat: 'Fixed', time: '84.2 s', badge: 'badge-danger', note: 'Capacity saturated, cannot recover during peak' },
              { strat: 'HPA', time: '41.6 s', badge: 'badge-warning', note: 'Downstream DB lock contention delays cooldown' },
              { strat: 'QueueBacklog', time: '38.0 s', badge: 'badge-warning', note: 'Backlog drain oscillation and flapping' },
              { strat: 'SCOPE-X', time: '1.2 s', badge: 'badge-active', note: 'Proactive admission gate prevents lock cascade' },
            ].map((r, i) => (
              <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px', background: '#FAFAFC', borderRadius: '12px', border: '1px solid #ECEEF3' }}>
                <div>
                  <div style={{ fontWeight: 700, fontSize: '13px', color: '#111' }}>{r.strat}</div>
                  <div style={{ fontSize: '11px', color: '#8C8E9B', marginTop: '2px' }}>{r.note}</div>
                </div>
                <span className={`pill-badge ${r.badge}`} style={{ fontSize: '12px', padding: '6px 14px' }}>
                  {r.time}
                </span>
              </div>
            ))}
          </div>
        </div>

      </div>
    </div>
  );
}
