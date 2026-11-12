import React, { useState } from 'react';
import { Lock, Code, Check } from 'lucide-react';

export default function ConstraintExplorerPanel({ constraints }) {
  const [copiedIdx, setCopiedIdx] = useState(null);

  const list = constraints || [
    { rule: "db_pool_size <= 25 AND rps > 400", type: "decision_tree", confidence: 0.94, support: 0.21, source: "learned", action: "SLO_VIOLATION", z3_expr: "And(db_pool_size <= 25, rps > 400)" },
    { rule: "checkout_replicas >= 4 AND db_pool_size >= 48", type: "regression", confidence: 0.88, support: 0.15, source: "learned", action: "DB_CEILING_BREACH", z3_expr: "And(replicas >= 4, db_pool_size >= 48)" },
    { rule: "cpu_util > 90 AND replicas < 3", type: "decision_tree", confidence: 0.91, support: 0.18, source: "verified", action: "LATENCY_SLO_BREACH", z3_expr: "And(cpu_util > 90, replicas < 3)" },
    { rule: "cache_enabled == False AND rps > 300", type: "sparse_regression", confidence: 0.79, support: 0.32, source: "learned", action: "HIGH_LATENCY", z3_expr: "And(Not(cache_enabled), rps > 300)" },
    { rule: "db_pool_size * replicas > 200", type: "verified", confidence: 0.99, support: 0.44, source: "z3_verified", action: "PG_CONN_LIMIT", z3_expr: "db_pool_size * replicas > 200" },
    { rule: "consumers < 2 AND rps > 500", type: "decision_tree", confidence: 0.83, support: 0.12, source: "learned", action: "QUEUE_OVERLOAD", z3_expr: "And(consumers < 2, rps > 500)" },
  ];

  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '20px' }}>
        
        {/* Left: Rule Cards */}
        <div className="jobgio-card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '18px' }}>
            <div>
              <h3 style={{ fontSize: '18px', fontWeight: 700, color: '#111111', letterSpacing: '-0.4px' }}>
                Discovered &amp; Verified Invariant Rules
              </h3>
              <p style={{ fontSize: '12px', color: '#8C8E9B', marginTop: '2px' }}>
                Synthesized via Decision Trees (CART), Sparse Lasso, and Z3 SMT verification
              </p>
            </div>
            <span className="pill-badge badge-dark">{list.length} Invariants</span>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {list.map((c, idx) => {
              const isVerified = c.source?.includes('verified');
              return (
                <div key={idx} style={{ border: '1px solid #ECEEF3', borderRadius: '16px', padding: '16px', background: '#FAFAFC' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                    <div style={{ fontSize: '13px', fontWeight: 700, color: '#111111' }}>
                      IF <code style={{ background: '#FFFFFF', border: '1px solid #E5E7EB', padding: '2px 8px', borderRadius: '6px' }}>{c.rule}</code>
                    </div>
                    <span className={`pill-badge ${isVerified ? 'badge-safe' : 'badge-pending'}`}>
                      {c.source}
                    </span>
                  </div>

                  <div style={{ fontSize: '12px', fontWeight: 600, color: '#D92D20', marginBottom: '10px' }}>
                    ⇒ {c.action}
                  </div>

                  <div style={{ display: 'flex', gap: '20px', fontSize: '11px', color: '#6C727F', marginBottom: '8px' }}>
                    <span>Confidence: <strong style={{ color: '#111' }}>{(c.confidence * 100).toFixed(0)}%</strong></span>
                    <span>Support: <strong style={{ color: '#111' }}>{(c.support * 100).toFixed(0)}%</strong></span>
                    <span>Inducer: <strong style={{ color: '#111' }}>{c.type}</strong></span>
                  </div>

                  {/* Confidence Progress Bar */}
                  <div style={{ width: '100%', height: '6px', background: '#EAECEF', borderRadius: '100px', overflow: 'hidden' }}>
                    <div style={{ width: `${c.confidence * 100}%`, height: '100%', background: '#111111', borderRadius: '100px' }}></div>
                  </div>

                  {/* Z3 Formula code snippet */}
                  <div style={{ marginTop: '10px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: '#FFFFFF', border: '1px solid #ECEEF3', padding: '6px 10px', borderRadius: '8px' }}>
                    <code style={{ fontSize: '11px', color: '#4B4F59' }}>{c.z3_expr}</code>
                    <button
                      onClick={() => {
                        navigator.clipboard?.writeText(c.z3_expr);
                        setCopiedIdx(idx);
                        setTimeout(() => setCopiedIdx(null), 1500);
                      }}
                      style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: '#8C8E9B' }}
                      title="Copy Z3 Expression"
                    >
                      {copiedIdx === idx ? <Check size={14} color="#108A00" /> : <Code size={14} />}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right: Invariant Validation Rules */}
        <div className="jobgio-card">
          <h3 style={{ fontSize: '18px', fontWeight: 700, color: '#111111', letterSpacing: '-0.4px', marginBottom: '4px' }}>
            Invariant Soundness
          </h3>
          <p style={{ fontSize: '12px', color: '#8C8E9B', marginBottom: '18px' }}>
            Empirical precision on held-out test splits
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
            <div style={{ padding: '14px', background: '#FAFAFC', borderRadius: '14px', border: '1px solid #ECEEF3' }}>
              <div style={{ fontSize: '11px', fontWeight: 600, color: '#8C8E9B' }}>GLOBAL POSTGRES CEILING</div>
              <div style={{ fontSize: '20px', fontWeight: 800, color: '#111', marginTop: '4px' }}>200 Max Connections</div>
              <div style={{ fontSize: '11px', color: '#4B4F59', marginTop: '4px' }}>Strict hardware limit preventing connection exhaustion cascade.</div>
            </div>

            <div style={{ padding: '14px', background: '#FAFAFC', borderRadius: '14px', border: '1px solid #ECEEF3' }}>
              <div style={{ fontSize: '11px', fontWeight: 600, color: '#8C8E9B' }}>HIGH-LOAD POOL INVARIANT</div>
              <div style={{ fontSize: '20px', fontWeight: 800, color: '#111', marginTop: '4px' }}>pool &gt; 25 @ &gt; 400 RPS</div>
              <div style={{ fontSize: '11px', color: '#4B4F59', marginTop: '4px' }}>Discovered by CART; proven with Z3 under 94% confidence.</div>
            </div>

            <div style={{ padding: '14px', background: '#FAFAFC', borderRadius: '14px', border: '1px solid #ECEEF3' }}>
              <div style={{ fontSize: '11px', fontWeight: 600, color: '#8C8E9B' }}>REPLICA HARDWARE FLOORS</div>
              <div style={{ fontSize: '20px', fontWeight: 800, color: '#111', marginTop: '4px' }}>1 ≤ Replicas ≤ 8</div>
              <div style={{ fontSize: '11px', color: '#4B4F59', marginTop: '4px' }}>Enforced by CP-SAT solver and Adaptive Decision Engine.</div>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
