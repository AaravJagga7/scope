import React, { useState } from 'react';
import { ArrowUpRight, CheckCircle, XCircle, ShieldAlert, MoreHorizontal, Download, ChevronDown } from 'lucide-react';

export default function LiveMonitoringPanel({ telemetryData, onRefresh }) {
  const [selectedProfile, setSelectedProfile] = useState('flash_sale');

  // SVG dual-bar chart calculation
  const maxRps = 800;
  const chartHeight = 180;
  const chartWidth = 580;

  const dataPoints = telemetryData?.slice(-16) || [];
  const barWidth = 14;
  const barGap = 4;
  const groupSpacing = 32;

  return (
    <div>
      {/* Top Controls */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '18px' }}>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <span style={{ fontSize: '13px', fontWeight: 600, color: '#6C727F' }}>Workload Profile:</span>
          {['flash_sale', 'ramp', 'normal'].map((p) => (
            <button
              key={p}
              onClick={() => setSelectedProfile(p)}
              style={{
                padding: '6px 14px',
                borderRadius: '100px',
                border: selectedProfile === p ? '1px solid #111' : '1px solid #E5E7EB',
                background: selectedProfile === p ? '#111' : '#FFF',
                color: selectedProfile === p ? '#FFF' : '#4B4F59',
                fontSize: '12px',
                fontWeight: 600,
                cursor: 'pointer',
                textTransform: 'capitalize'
              }}
            >
              {p.replace('_', ' ')}
            </button>
          ))}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#108A00' }}></span>
          <span style={{ fontSize: '12px', fontWeight: 600, color: '#108A00' }}>Telemetric Loop Active (1Hz)</span>
        </div>
      </div>

      {/* Middle Grid: Statistics Chart + Pending Approvals */}
      <div style={{ display: 'grid', gridTemplateColumns: '2.8fr 1.6fr', gap: '20px', marginBottom: '24px' }}>
        
        {/* Left Card: Dual-Bar Statistics Chart */}
        <div className="jobgio-card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '18px' }}>
            <div>
              <h3 style={{ fontSize: '18px', fontWeight: 700, color: '#111111', letterSpacing: '-0.4px' }}>Statistics</h3>
              <p style={{ fontSize: '12px', color: '#8C8E9B', marginTop: '2px' }}>Offered workload throughput vs Admitted traffic rate</p>
            </div>
            <div style={{ display: 'flex', gap: '14px', alignItems: 'center' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <span style={{ width: '10px', height: '10px', borderRadius: '2px', background: '#111111' }}></span>
                <span style={{ fontSize: '11px', fontWeight: 600, color: '#111111' }}>Offered Load</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <span style={{ width: '10px', height: '10px', borderRadius: '2px', background: '#D8DBE5' }}></span>
                <span style={{ fontSize: '11px', fontWeight: 600, color: '#8C8E9B' }}>Admitted Rate</span>
              </div>
            </div>
          </div>

          {/* Clean Vector Dual-Bar Graph */}
          <div style={{ width: '100%', overflowX: 'auto', paddingTop: '10px' }}>
            <svg viewBox={`0 0 ${chartWidth} ${chartHeight + 40}`} style={{ width: '100%', height: '220px' }}>
              {/* Horizontal Grid lines */}
              {[0, 200, 400, 600, 800].map((level) => {
                const y = chartHeight - (level / maxRps) * chartHeight + 10;
                return (
                  <g key={level}>
                    <line x1="30" y1={y} x2={chartWidth} y2={y} stroke="#F0F1F5" strokeWidth="1" />
                    <text x="5" y={y + 4} fill="#8C8E9B" fontSize="9" fontFamily="Plus Jakarta Sans">{level}</text>
                  </g>
                );
              })}

              {/* Dual Bars */}
              {dataPoints.map((d, idx) => {
                const groupX = 40 + idx * groupSpacing;
                const hOffered = (d.rps / maxRps) * chartHeight;
                const hAdmitted = ((d.rps * (d.p95_ms > 600 ? 0.88 : 1.0)) / maxRps) * chartHeight;
                const yOffered = chartHeight - hOffered + 10;
                const yAdmitted = chartHeight - hAdmitted + 10;

                return (
                  <g key={idx}>
                    {/* Black bar: Offered */}
                    <rect
                      x={groupX}
                      y={yOffered}
                      width={barWidth}
                      height={hOffered}
                      fill="#111111"
                      rx="3"
                    />
                    {/* Gray bar: Admitted */}
                    <rect
                      x={groupX + barWidth + barGap}
                      y={yAdmitted}
                      width={barWidth}
                      height={hAdmitted}
                      fill="#D8DBE5"
                      rx="3"
                    />
                    {/* Timestamp label */}
                    <text
                      x={groupX + barWidth}
                      y={chartHeight + 28}
                      textAnchor="middle"
                      fill="#8C8E9B"
                      fontSize="9"
                    >
                      +{d.t}s
                    </text>
                  </g>
                );
              })}
            </svg>
          </div>
        </div>

        {/* Right Card: Pending Approvals & Safety Decisions */}
        <div className="jobgio-card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
            <div>
              <h3 style={{ fontSize: '18px', fontWeight: 700, color: '#111111', letterSpacing: '-0.4px' }}>Pending Approvals</h3>
              <p style={{ fontSize: '12px', color: '#8C8E9B', marginTop: '2px' }}>Safety Guard scaling decisions</p>
            </div>
            <div style={{ width: '24px', height: '24px', borderRadius: '50%', border: '1px solid #E5E7EB', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#8C8E9B', fontSize: '12px' }}>
              ⊖
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column' }}>
            <div className="approval-row">
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <div style={{ width: '38px', height: '38px', borderRadius: '12px', background: '#F5F6FA', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '16px' }}>
                  🍎
                </div>
                <div>
                  <div style={{ fontSize: '13px', fontWeight: 700, color: '#111111' }}>Checkout Scale-Out</div>
                  <div style={{ fontSize: '11px', color: '#8C8E9B' }}>Target 5 Pods · DB Limit Breach</div>
                </div>
              </div>
              <span className="pill-badge badge-danger">Rejected</span>
            </div>

            <div className="approval-row">
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <div style={{ width: '38px', height: '38px', borderRadius: '12px', background: '#F5F6FA', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '16px' }}>
                  🎨
                </div>
                <div>
                  <div style={{ fontSize: '13px', fontWeight: 700, color: '#111111' }}>DB Pool Expansion</div>
                  <div style={{ fontSize: '11px', color: '#8C8E9B' }}>Resize to 58 conns/pod</div>
                </div>
              </div>
              <span className="pill-badge badge-danger">Rejected</span>
            </div>

            <div className="approval-row">
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <div style={{ width: '38px', height: '38px', borderRadius: '12px', background: '#F5F6FA', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '16px' }}>
                  🚘
                </div>
                <div>
                  <div style={{ fontSize: '13px', fontWeight: 700, color: '#111111' }}>Admission Shedding</div>
                  <div style={{ fontSize: '11px', color: '#8C8E9B' }}>Shed 12% non-critical traffic</div>
                </div>
              </div>
              <span className="pill-badge badge-active">Approved</span>
            </div>

            <div className="approval-row">
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <div style={{ width: '38px', height: '38px', borderRadius: '12px', background: '#F5F6FA', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '16px' }}>
                  ⚡
                </div>
                <div>
                  <div style={{ fontSize: '13px', fontWeight: 700, color: '#111111' }}>State Hold Invariant</div>
                  <div style={{ fontSize: '11px', color: '#8C8E9B' }}>Preserve 4 pods / 48 pool</div>
                </div>
              </div>
              <span className="pill-badge badge-safe">Recommended</span>
            </div>
          </div>
        </div>

      </div>

      {/* Bottom Section: Manage Telemetry & Nodes Table */}
      <div className="jobgio-card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '18px' }}>
          <div>
            <h3 style={{ fontSize: '18px', fontWeight: 700, color: '#111111', letterSpacing: '-0.4px' }}>
              Manage telemetry & nodes
            </h3>
            <p style={{ fontSize: '12px', color: '#8C8E9B', marginTop: '2px' }}>
              Active pods, connection footprints, and live operational latencies
            </p>
          </div>

          <div style={{ display: 'flex', gap: '10px' }}>
            <div style={{
              background: '#F4F5F8',
              borderRadius: '100px',
              padding: '6px 14px',
              fontSize: '12px',
              fontWeight: 600,
              color: '#4B4F59',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              cursor: 'pointer'
            }}>
              Status <ChevronDown size={14} />
            </div>
            <div style={{
              background: '#111111',
              color: '#FFFFFF',
              borderRadius: '100px',
              padding: '6px 16px',
              fontSize: '12px',
              fontWeight: 600,
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              cursor: 'pointer'
            }}>
              Export <Download size={14} />
            </div>
          </div>
        </div>

        <table className="clean-table">
          <thead>
            <tr>
              <th style={{ width: '40px' }}><input type="checkbox" defaultChecked style={{ accentColor: '#111111' }} /></th>
              <th>Service Pod</th>
              <th>Namespace / Node</th>
              <th>Replicas & Pool</th>
              <th>Status</th>
              <th>p95 Latency</th>
              <th>Last Heartbeat</th>
              <th style={{ textAlign: 'center' }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><input type="checkbox" defaultChecked style={{ accentColor: '#111111' }} /></td>
              <td style={{ fontWeight: 700 }}>checkout-service-7f9a</td>
              <td style={{ color: '#6C727F' }}>google / k8s-worker-1</td>
              <td>4 replicas · pool=48</td>
              <td><span className="pill-badge badge-active">Active</span></td>
              <td style={{ fontWeight: 700, color: '#111' }}>348.2 ms</td>
              <td style={{ color: '#8C8E9B' }}>Just now</td>
              <td style={{ textAlign: 'center', color: '#8C8E9B', cursor: 'pointer' }}><MoreHorizontal size={18} /></td>
            </tr>
            <tr>
              <td><input type="checkbox" style={{ accentColor: '#111111' }} /></td>
              <td style={{ fontWeight: 700 }}>order-service-6b4c</td>
              <td style={{ color: '#6C727F' }}>amazon / k8s-worker-2</td>
              <td>2 replicas · pool=20</td>
              <td><span className="pill-badge badge-active">Active</span></td>
              <td style={{ fontWeight: 700, color: '#111' }}>182.4 ms</td>
              <td style={{ color: '#8C8E9B' }}>2 mins ago</td>
              <td style={{ textAlign: 'center', color: '#8C8E9B', cursor: 'pointer' }}><MoreHorizontal size={18} /></td>
            </tr>
            <tr>
              <td><input type="checkbox" style={{ accentColor: '#111111' }} /></td>
              <td style={{ fontWeight: 700 }}>payment-gateway-proxy</td>
              <td style={{ color: '#6C727F' }}>paypal / k8s-worker-3</td>
              <td>3 replicas · pool=30</td>
              <td><span className="pill-badge badge-safe">Verified</span></td>
              <td style={{ fontWeight: 700, color: '#111' }}>210.0 ms</td>
              <td style={{ color: '#8C8E9B' }}>5 mins ago</td>
              <td style={{ textAlign: 'center', color: '#8C8E9B', cursor: 'pointer' }}><MoreHorizontal size={18} /></td>
            </tr>
            <tr>
              <td><input type="checkbox" style={{ accentColor: '#111111' }} /></td>
              <td style={{ fontWeight: 700 }}>inventory-redis-cache</td>
              <td style={{ color: '#6C727F' }}>redis / memory-store</td>
              <td>1 node · cache=hit (94%)</td>
              <td><span className="pill-badge badge-pending">Cooldown</span></td>
              <td style={{ fontWeight: 700, color: '#111' }}>12.8 ms</td>
              <td style={{ color: '#8C8E9B' }}>10 mins ago</td>
              <td style={{ textAlign: 'center', color: '#8C8E9B', cursor: 'pointer' }}><MoreHorizontal size={18} /></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

