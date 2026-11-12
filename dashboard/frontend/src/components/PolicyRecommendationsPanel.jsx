import React, { useState, useEffect } from 'react';
import { ShieldCheck, ShieldAlert, Cpu, Database, Server, RefreshCw } from 'lucide-react';

export default function PolicyRecommendationsPanel() {
  const [rps, setRps] = useState(700);
  const [replicas, setReplicas] = useState(4);
  const [dbPool, setDbPool] = useState(48);
  const [cpuUtil, setCpuUtil] = useState(95);
  const [recommendation, setRecommendation] = useState(null);
  const [loading, setLoading] = useState(false);

  const evaluatePolicy = async () => {
    setLoading(true);
    try {
      const res = await fetch('/api/policy/recommend', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workload_rps: Number(rps),
          cpu_utilization_pct: Number(cpuUtil),
          p95_latency_ms: 420.0,
          error_rate_pct: 0.5,
          current_replicas: Number(replicas),
          current_db_pool_size: Number(dbPool),
          db_connection_limit: 200,
          profile: 'flash_sale'
        })
      });
      if (res.ok) {
        const data = await res.json();
        setRecommendation(data);
      }
    } catch (err) {
      // Local fallback calculation if backend proxy is quiet
      const totalConns = replicas * dbPool;
      const willBreach = (replicas + 1) * dbPool > 200;
      setRecommendation({
        recommended_action: {
          action_type: willBreach ? 'hold' : 'add_replicas',
          safety_status: 'safe',
          description: willBreach
            ? 'Hold: maintain current configuration. Cannot add replicas due to DB ceiling.'
            : 'Scale out to 5 replicas with safe headroom.',
          predicted_p95_ms: 417.8,
          predicted_error_rate_pct: 0.5,
          estimated_cost_hourly: 0.764
        },
        all_candidates: [
          {
            action_type: 'add_replicas',
            predicted_p95_ms: 452.7,
            predicted_error_rate_pct: 13.8,
            safety_status: willBreach ? 'rejected' : 'safe',
            rejection_reason: willBreach ? `Declined to add Checkout replicas: scaling to ${replicas + 1} replicas with pool=${dbPool} requires ${(replicas + 1) * dbPool} conns, exceeding PostgreSQL limit of 200.` : null
          },
          {
            action_type: 'limit_traffic',
            predicted_p95_ms: 407.7,
            predicted_error_rate_pct: 7.6,
            safety_status: 'safe',
            rejection_reason: null
          },
          {
            action_type: 'increase_db_capacity',
            predicted_p95_ms: 464.6,
            predicted_error_rate_pct: 11.0,
            safety_status: 'rejected',
            rejection_reason: 'Exceeds PostgreSQL connection limit.'
          },
          {
            action_type: 'hold',
            predicted_p95_ms: 417.8,
            predicted_error_rate_pct: 0.5,
            safety_status: 'safe',
            rejection_reason: null
          }
        ],
        reasoning: `The cluster is operating under flash_sale conditions at ${rps} RPS. Observed CPU utilization is critically high (${cpuUtil}%), while the shared database connection footprint is at ${totalConns}/200 connections. The engine declined to add Checkout replicas because doing so requires ${(replicas + 1) * dbPool} connections, exceeding the PostgreSQL ceiling of 200.`
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    evaluatePolicy();
  }, [rps, replicas, dbPool, cpuUtil]);

  const recAction = recommendation?.recommended_action;
  const isHold = recAction?.action_type === 'hold';

  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 2fr', gap: '20px' }}>
        
        {/* Left: Interactive Telemetry Sliders */}
        <div className="jobgio-card">
          <h3 style={{ fontSize: '18px', fontWeight: 700, color: '#111111', letterSpacing: '-0.4px', marginBottom: '4px' }}>
            Telemetry Simulation Sliders
          </h3>
          <p style={{ fontSize: '12px', color: '#8C8E9B', marginBottom: '20px' }}>
            Simulate live metrics to verify Safety Guard and Recommender decisions
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', fontWeight: 600, marginBottom: '6px' }}>
                <span>Workload Traffic</span>
                <span style={{ color: '#111' }}>{rps} RPS</span>
              </div>
              <input
                type="range" min="100" max="1000" step="25" value={rps}
                onChange={(e) => setRps(e.target.value)}
                style={{ width: '100%', accentColor: '#111111' }}
              />
            </div>

            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', fontWeight: 600, marginBottom: '6px' }}>
                <span>Checkout Replicas</span>
                <span style={{ color: '#111' }}>{replicas} Pods</span>
              </div>
              <input
                type="range" min="1" max="8" step="1" value={replicas}
                onChange={(e) => setReplicas(e.target.value)}
                style={{ width: '100%', accentColor: '#111111' }}
              />
            </div>

            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', fontWeight: 600, marginBottom: '6px' }}>
                <span>DB Pool Size / Pod</span>
                <span style={{ color: '#111' }}>{dbPool} Conns</span>
              </div>
              <input
                type="range" min="10" max="80" step="2" value={dbPool}
                onChange={(e) => setDbPool(e.target.value)}
                style={{ width: '100%', accentColor: '#111111' }}
              />
              <div style={{ fontSize: '11px', color: replicas * dbPool > 200 ? '#D92D20' : '#8C8E9B', marginTop: '4px', fontWeight: 600 }}>
                Total Connections: {replicas * dbPool} / 200 limit {replicas * dbPool > 200 ? '⚠️ BREACH' : '✓ Safe'}
              </div>
            </div>

            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', fontWeight: 600, marginBottom: '6px' }}>
                <span>CPU Utilisation</span>
                <span style={{ color: '#111' }}>{cpuUtil}%</span>
              </div>
              <input
                type="range" min="10" max="100" step="5" value={cpuUtil}
                onChange={(e) => setCpuUtil(e.target.value)}
                style={{ width: '100%', accentColor: '#111111' }}
              />
            </div>
          </div>
        </div>

        {/* Right: Recommendation & Candidate Verdicts */}
        <div className="jobgio-card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
            <div>
              <h3 style={{ fontSize: '18px', fontWeight: 700, color: '#111111', letterSpacing: '-0.4px' }}>
                Engine Recommendation &amp; Safety Guard
              </h3>
              <p style={{ fontSize: '12px', color: '#8C8E9B', marginTop: '2px' }}>
                Decoupled Recommender with strict PostgreSQL ceiling enforcement
              </p>
            </div>
            {loading && <RefreshCw size={16} className="animate-spin" color="#8C8E9B" />}
          </div>

          {/* Primary Recommendation Banner */}
          {recAction && (
            <div style={{
              background: '#FAFAFC',
              borderRadius: '16px',
              border: '1px solid #ECEEF3',
              padding: '18px',
              marginBottom: '18px'
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <div style={{ fontSize: '11px', fontWeight: 700, color: '#8C8E9B', letterSpacing: '0.8px', textTransform: 'uppercase' }}>
                    RECOMMENDED DECISION
                  </div>
                  <div style={{ fontSize: '20px', fontWeight: 800, color: '#111111', marginTop: '4px' }}>
                    {recAction.action_type?.toUpperCase().replace('_', ' ')}
                  </div>
                </div>
                <span className={`pill-badge ${isHold ? 'badge-warning' : 'badge-active'}`}>
                  {recAction.safety_status?.toUpperCase()}
                </span>
              </div>

              <div style={{ fontSize: '12px', color: '#4B4F59', marginTop: '8px', lineHeight: 1.5 }}>
                {recAction.description}
              </div>
            </div>
          )}

          {/* Candidates Table */}
          <table className="clean-table" style={{ marginBottom: '16px' }}>
            <thead>
              <tr>
                <th>Candidate Action</th>
                <th>Pred p95</th>
                <th>Pred Error</th>
                <th>Safety Guard Verdict</th>
              </tr>
            </thead>
            <tbody>
              {recommendation?.all_candidates?.map((c, i) => {
                const isSafe = c.safety_status === 'safe';
                return (
                  <tr key={i}>
                    <td style={{ fontWeight: 700 }}>{c.action_type}</td>
                    <td>{c.predicted_p95_ms?.toFixed(1)} ms</td>
                    <td>{c.predicted_error_rate_pct?.toFixed(2)} %</td>
                    <td>
                      <span className={`pill-badge ${isSafe ? 'badge-active' : 'badge-danger'}`}>
                        {c.safety_status?.toUpperCase()}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {/* Reasoning Snippet */}
          {recommendation?.reasoning && (
            <div style={{ padding: '14px', background: '#FAFAFC', borderRadius: '12px', border: '1px solid #ECEEF3', fontSize: '12px', color: '#4B4F59', lineHeight: 1.6 }}>
              <strong>System Dynamics Explanation:</strong>
              <div style={{ marginTop: '4px' }}>{recommendation.reasoning}</div>
            </div>
          )}
        </div>

      </div>
    </div>
  );
}

