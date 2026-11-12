import React from 'react';
import {
  Zap,
  Activity,
  AlertTriangle,
  Lock,
  FlaskConical,
  CheckCircle2,
  Sliders,
  Scale,
  Settings
} from 'lucide-react';

export default function Sidebar({ activeTab, setActiveTab }) {
  const navItems = [
    { id: 'dashboard', label: 'Dashboard', icon: Activity },
    { id: 'failures', label: 'Failure Visualisation', icon: AlertTriangle },
    { id: 'constraints', label: 'Constraint Explorer', icon: Lock },
    { id: 'experiments', label: 'Experiment Tracker', icon: FlaskConical },
    { id: 'z3', label: 'Z3 Verification', icon: CheckCircle2 },
    { id: 'policy', label: 'Policy Engine', icon: Sliders },
    { id: 'comparison', label: 'Before / After', icon: Scale },
  ];

  return (
    <aside style={{
      width: '260px',
      background: '#FFFFFF',
      borderRight: '1px solid #ECEEF3',
      display: 'flex',
      flexDirection: 'column',
      justifyContent: 'space-between',
      padding: '24px 18px',
      height: '100vh',
      position: 'sticky',
      top: 0,
      flexShrink: 0
    }}>
      <div>
        {/* Logo matching Jobgio header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '0 8px 24px', borderBottom: '1px solid #F2F3F7', marginBottom: '20px' }}>
          <div style={{
            width: '36px',
            height: '36px',
            background: '#111111',
            borderRadius: '10px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#FFFFFF'
          }}>
            <Zap size={20} fill="#FFFFFF" />
          </div>
          <div>
            <div style={{ fontSize: '18px', fontWeight: 800, color: '#111111', letterSpacing: '-0.5px' }}>ScopeX</div>
            <div style={{ fontSize: '10px', color: '#8C8E9B', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.8px' }}>Systems Intelligence</div>
          </div>
        </div>

        {/* Navigation list */}
        <nav style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = activeTab === item.id;
            return (
              <button
                key={item.id}
                onClick={() => setActiveTab(item.id)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '12px',
                  padding: '11px 16px',
                  borderRadius: '100px',
                  border: 'none',
                  background: isActive ? '#111111' : 'transparent',
                  color: isActive ? '#FFFFFF' : '#6C727F',
                  fontWeight: isActive ? 600 : 500,
                  fontSize: '13px',
                  cursor: 'pointer',
                  textAlign: 'left',
                  width: '100%',
                  transition: 'all 0.15s ease',
                  boxShadow: isActive ? '0 4px 12px rgba(0, 0, 0, 0.12)' : 'none'
                }}
              >
                <Icon size={17} strokeWidth={isActive ? 2.4 : 1.8} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
      </div>

      {/* Operator Profile Widget at Bottom */}
      <div style={{
        background: '#F8F9FC',
        borderRadius: '16px',
        padding: '12px 14px',
        display: 'flex',
        alignItems: 'center',
        gap: '12px'
      }}>
        <div style={{
          width: '36px',
          height: '36px',
          borderRadius: '50%',
          background: '#111111',
          color: '#FFFFFF',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontWeight: 700,
          fontSize: '13px'
        }}>
          OP
        </div>
        <div style={{ overflow: 'hidden' }}>
          <div style={{ fontSize: '12px', fontWeight: 700, color: '#111111', whiteSpace: 'nowrap', textOverflow: 'ellipsis' }}>
            Cluster Operator
          </div>
          <div style={{ fontSize: '10px', color: '#108A00', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '4px' }}>
            <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#108A00' }}></span>
            Online · Live
          </div>
        </div>
      </div>
    </aside>
  );
}

