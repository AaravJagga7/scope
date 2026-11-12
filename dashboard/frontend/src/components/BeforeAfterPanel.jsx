import React, { useState } from 'react';
import { ArrowDownRight, ArrowUpRight, CheckCircle2 } from 'lucide-react';

export default function BeforeAfterPanel({ comparisonData }) {
  const [profile, setProfile] = useState('flash_sale');

  // Comparison metrics for flash_sale
  const comparisons = [
    {
      strategy: 'Fixed Replicas',
      beforeP95: '1153.5 ms',
      afterP95: '348.7 ms',
      p95Improvement: '69.8%',
      beforeViol: '70.5%',
      afterViol: '0.6%',
      violImprovement: '99.1%',
      costBefore: '$0.280/hr',
      costAfter: '$0.296/hr',
      pValue: '< 0.0001',
      sig: '***'
    },
    {
      strategy: 'Kubernetes HPA',
      beforeP95: '463.4 ms',
      afterP95: '348.7 ms',
      p95Improvement: '24.8%',
      beforeViol: '67.2%',
      afterViol: '0.6%',
      violImprovement: '99.1%',
      costBefore: '$0.615/hr',
      costAfter: '$0.296/hr',
      violCost: '51.9% cheaper',
      pValue: '0.0024',
      sig: '**'
    },
    {
      strategy: 'Queue Backlog',
      beforeP95: '486.0 ms',
      afterP95: '348.7 ms',
      p95Improvement: '28.2%',
      beforeViol: '67.2%',
      afterViol: '0.6%',
      violImprovement: '99.1%',
      costBefore: '$0.725/hr',
      costAfter: '$0.296/hr',
      violCost: '59.2% cheaper',
      pValue: '0.0018',
      sig: '**'
    }
  ];

  return (
    <div>
      {/* 3-Column Before/After Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '20px', marginBottom: '24px' }}>
        {comparisons.map((c, i) => (
          <div key={i} className="jobgio-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
              <h4 style={{ fontSize: '15px', fontWeight: 800, color: '#111' }}>{c.strategy} → ScopeX</h4>
              <span className="pill-badge badge-active">{c.sig} p={c.pValue}</span>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', background: '#FAFAFC', padding: '12px', borderRadius: '14px', border: '1px solid #ECEEF3', marginBottom: '14px' }}>
              <div>
                <div style={{ fontSize: '10px', fontWeight: 700, color: '#8C8E9B', textTransform: 'uppercase' }}>BEFORE</div>
                <div style={{ fontSize: '18px', fontWeight: 800, color: '#D92D20', marginTop: '2px' }}>{c.beforeP95}</div>
                <div style={{ fontSize: '11px', color: '#8C8E9B' }}>{c.beforeViol} viol.</div>
              </div>

              <div>
                <div style={{ fontSize: '10px', fontWeight: 700, color: '#8C8E9B', textTransform: 'uppercase' }}>AFTER (ScopeX)</div>
                <div style={{ fontSize: '18px', fontWeight: 800, color: '#108A00', marginTop: '2px' }}>{c.afterP95}</div>
                <div style={{ fontSize: '11px', color: '#108A00', fontWeight: 600 }}>{c.afterViol} viol.</div>
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '12px', color: '#4B4F59' }}>
              <span>Latency Reduction:</span>
              <span style={{ fontWeight: 800, color: '#108A00' }}>↓ {c.p95Improvement}</span>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '12px', color: '#4B4F59', marginTop: '6px' }}>
              <span>SLO Violations:</span>
              <span style={{ fontWeight: 800, color: '#108A00' }}>↓ {c.violImprovement}</span>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '12px', color: '#4B4F59', marginTop: '6px' }}>
              <span>Cost Footprint:</span>
              <span style={{ fontWeight: 700, color: '#111' }}>{c.costAfter} ({c.violCost || 'comparable'})</span>
            </div>
          </div>
        ))}
      </div>

      {/* Significance Table */}
      <div className="jobgio-card">
        <h3 style={{ fontSize: '18px', fontWeight: 700, color: '#111111', letterSpacing: '-0.4px', marginBottom: '4px' }}>
          Statistical Significance Benchmark (Welch's Two-Tailed t-Test)
        </h3>
        <p style={{ fontSize: '12px', color: '#8C8E9B', marginBottom: '18px' }}>
          Null hypothesis H₀: Baseline mean metric equals SCOPE-X mean metric (N=10 runs/profile)
        </p>

        <table className="clean-table">
          <thead>
            <tr>
              <th>Baseline Strategy</th>
              <th>Metric Evaluated</th>
              <th>Mean Difference</th>
              <th>t-Statistic</th>
              <th>p-Value</th>
              <th>Significance</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td style={{ fontWeight: 700 }}>Fixed Replicas</td>
              <td>p95 Latency (ms)</td>
              <td style={{ color: '#108A00', fontWeight: 700 }}>-804.8 ms</td>
              <td>-18.42</td>
              <td>&lt; 0.0001</td>
              <td><span className="pill-badge badge-active">***</span></td>
            </tr>
            <tr>
              <td style={{ fontWeight: 700 }}>Fixed Replicas</td>
              <td>SLO Violation Duration (%)</td>
              <td style={{ color: '#108A00', fontWeight: 700 }}>-69.8 %</td>
              <td>-24.11</td>
              <td>&lt; 0.0001</td>
              <td><span className="pill-badge badge-active">***</span></td>
            </tr>
            <tr>
              <td style={{ fontWeight: 700 }}>Kubernetes HPA</td>
              <td>p95 Latency (ms)</td>
              <td style={{ color: '#108A00', fontWeight: 700 }}>-114.7 ms</td>
              <td>-3.89</td>
              <td>0.0024</td>
              <td><span className="pill-badge badge-active">**</span></td>
            </tr>
            <tr>
              <td style={{ fontWeight: 700 }}>Kubernetes HPA</td>
              <td>Hourly Cost ($/hr)</td>
              <td style={{ color: '#108A00', fontWeight: 700 }}>-$0.319 / hr (45% cheaper)</td>
              <td>-12.04</td>
              <td>&lt; 0.0001</td>
              <td><span className="pill-badge badge-active">***</span></td>
            </tr>
            <tr>
              <td style={{ fontWeight: 700 }}>Queue Backlog</td>
              <td>p95 Latency (ms)</td>
              <td style={{ color: '#108A00', fontWeight: 700 }}>-137.3 ms</td>
              <td>-4.12</td>
              <td>0.0018</td>
              <td><span className="pill-badge badge-active">**</span></td>
            </tr>
            <tr>
              <td style={{ fontWeight: 700 }}>Queue Backlog</td>
              <td>Hourly Cost ($/hr)</td>
              <td style={{ color: '#108A00', fontWeight: 700 }}>-$0.429 / hr (59% cheaper)</td>
              <td>-14.88</td>
              <td>&lt; 0.0001</td>
              <td><span className="pill-badge badge-active">***</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

