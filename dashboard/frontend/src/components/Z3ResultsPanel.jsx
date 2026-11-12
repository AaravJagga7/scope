import React from 'react';
import { CheckCircle2, AlertOctagon, RefreshCw } from 'lucide-react';

export default function Z3ResultsPanel({ z3Data }) {
  const entries = z3Data || [
    { iteration: 1, status: "SAT", counterexample: { replicas: 5, db_pool_size: 48 }, real_p95_ms: 712.0, constraint: "db_pool_size <= 25 AND rps > 400", action: "REFINE", verdict: "Counterexample found: 5 * 48 = 240 conns breaches PostgreSQL ceiling." },
    { iteration: 2, status: "SAT", counterexample: { replicas: 4, db_pool_size: 52 }, real_p95_ms: 684.0, constraint: "total_conns <= 200", action: "REFINE", verdict: "Added ceiling constraint: total_db_conns <= 200" },
    { iteration: 3, status: "UNSAT", counterexample: null, real_p95_ms: null, constraint: "total_conns <= 200 (refined)", action: "VERIFIED", verdict: "No counterexample exists within search space. Constraint formally verified." },
  ];

  return (
    <div>
      <div className="jobgio-card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <div>
            <h3 style={{ fontSize: '18px', fontWeight: 700, color: '#111111', letterSpacing: '-0.4px' }}>
              Counterexample-Guided Inductive Synthesis (CEGIS Loop)
            </h3>
            <p style={{ fontSize: '12px', color: '#8C8E9B', marginTop: '2px' }}>
              Formal verification in the model vs Ground truth validation on the real cluster
            </p>
          </div>
          <span className="pill-badge badge-active">SMT Solver · Z3 Engine</span>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '14px', marginTop: '12px' }}>
          {entries.map((e, idx) => {
            const isSat = e.status === 'SAT';
            const Icon = isSat ? AlertOctagon : CheckCircle2;
            const badgeCls = isSat ? 'badge-danger' : 'badge-active';

            return (
              <div key={idx} style={{ border: '1px solid #ECEEF3', borderRadius: '16px', padding: '18px', background: '#FAFAFC' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <div style={{
                      width: '32px',
                      height: '32px',
                      borderRadius: '8px',
                      background: isSat ? '#FEECEB' : '#E9F8EE',
                      color: isSat ? '#D92D20' : '#108A00',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center'
                    }}>
                      <Icon size={18} />
                    </div>
                    <div>
                      <div style={{ fontWeight: 700, fontSize: '14px', color: '#111' }}>
                        Iteration {e.iteration} · {e.status === 'SAT' ? 'Counterexample Discovered' : 'Formally Verified'}
                      </div>
                      <div style={{ fontSize: '11px', color: '#8C8E9B' }}>
                        Formula: <code>{e.constraint}</code>
                      </div>
                    </div>
                  </div>

                  <span className={`pill-badge ${badgeCls}`}>{e.status}</span>
                </div>

                {e.counterexample && (
                  <div style={{ marginTop: '10px', padding: '10px 14px', background: '#FFFFFF', borderRadius: '10px', border: '1px solid #ECEEF3', display: 'flex', gap: '16px', alignItems: 'center', fontSize: '12px' }}>
                    <span style={{ fontWeight: 600, color: '#D92D20' }}>Counterexample:</span>
                    {Object.entries(e.counterexample).map(([k, v]) => (
                      <span key={k} style={{ color: '#111' }}><strong>{k}:</strong> {v}</span>
                    ))}
                    {e.real_p95_ms && (
                      <span style={{ color: '#D92D20', marginLeft: 'auto', fontWeight: 600 }}>
                        Real Measured p95: {e.real_p95_ms}ms (Violated)
                      </span>
                    )}
                  </div>
                )}

                <div style={{ fontSize: '12px', color: '#4B4F59', marginTop: '10px', lineHeight: 1.5 }}>
                  <strong>Refinement Action:</strong> {e.verdict}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
