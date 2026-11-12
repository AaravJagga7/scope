import React from 'react';
import { Zap, Layers, Database, Link2, Target, Clock } from 'lucide-react';

export default function QuickStatsHeader({ stats }) {
  const statCards = [
    { icon: Zap, label: 'Target RPS', value: stats?.workload_rps || '700', isHighlight: false },
    { icon: Layers, label: 'Replicas', value: stats?.replicas || '4', isHighlight: false },
    { icon: Database, label: 'DB Pool Size', value: stats?.db_pool_size || '48', isHighlight: true }, // Highlighted card matching Jobgio
    { icon: Link2, label: 'Active Conns', value: stats?.total_db_connections || '192', isHighlight: false },
    { icon: Target, label: 'SLO Violations', value: '0.6%', isHighlight: false },
    { icon: Clock, label: 'p95 Latency', value: `${stats?.p95_latency_ms || '348.7'}ms`, isHighlight: false },
  ];

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '220px 1fr',
      gap: '24px',
      marginBottom: '24px',
      alignItems: 'center'
    }}>
      <div>
        <h2 style={{ fontSize: '20px', fontWeight: 800, color: '#111111', letterSpacing: '-0.5px' }}>
          Quick Stats
        </h2>
        <p style={{ fontSize: '12px', color: '#8C8E9B', marginTop: '4px', lineHeight: 1.4 }}>
          Your cluster telemetry for the current live evaluation window.
        </p>
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(6, 1fr)',
        gap: '12px'
      }}>
        {statCards.map((c, i) => {
          const Icon = c.icon;
          return (
            <div key={i} className="stat-pill">
              <div className={`stat-icon-wrapper ${c.isHighlight ? 'highlighted' : ''}`}>
                <Icon size={16} />
              </div>
              <div className="stat-val">{c.value}</div>
              <div className="stat-lbl">{c.label}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
