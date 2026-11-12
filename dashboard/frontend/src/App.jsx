import React, { useState, useEffect } from 'react';
import Sidebar from './components/Sidebar';
import QuickStatsHeader from './components/QuickStatsHeader';
import LiveMonitoringPanel from './components/LiveMonitoringPanel';
import FailureVizPanel from './components/FailureVizPanel';
import ConstraintExplorerPanel from './components/ConstraintExplorerPanel';
import ExperimentTrackerPanel from './components/ExperimentTrackerPanel';
import Z3ResultsPanel from './components/Z3ResultsPanel';
import PolicyRecommendationsPanel from './components/PolicyRecommendationsPanel';
import BeforeAfterPanel from './components/BeforeAfterPanel';

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [stats, setStats] = useState(null);
  const [telemetry, setTelemetry] = useState([]);
  const [failures, setFailures] = useState(null);
  const [constraints, setConstraints] = useState([]);
  const [experiments, setExperiments] = useState(null);
  const [z3Logs, setZ3Logs] = useState([]);
  const [comparison, setComparison] = useState(null);

  // Fetch initial data from FastAPI backend with fallback
  useEffect(() => {
    const fetchData = async () => {
      try {
        const statsRes = await fetch('/api/quick-stats');
        if (statsRes.ok) setStats(await statsRes.json());
      } catch (e) {
        setStats({ workload_rps: 700, replicas: 4, db_pool_size: 48, total_db_connections: 192, p95_latency_ms: 348.7 });
      }

      try {
        const teleRes = await fetch('/api/telemetry/live?n=24');
        if (teleRes.ok) setTelemetry(await teleRes.json());
      } catch (e) {
        // Fallback synthetic steps
        setTelemetry(
          Array.from({ length: 16 }).map((_, i) => ({
            t: (i + 1) * 2,
            rps: 650 + Math.sin(i) * 60,
            replicas: 4,
            p95_ms: 340 + Math.cos(i) * 40
          }))
        );
      }

      try {
        const failRes = await fetch('/api/failures');
        if (failRes.ok) setFailures(await failRes.json());
      } catch (e) {}

      try {
        const cRes = await fetch('/api/constraints');
        if (cRes.ok) setConstraints(await cRes.json());
      } catch (e) {}

      try {
        const expRes = await fetch('/api/experiments');
        if (expRes.ok) setExperiments(await expRes.json());
      } catch (e) {}

      try {
        const z3Res = await fetch('/api/z3');
        if (z3Res.ok) setZ3Logs(await z3Res.json());
      } catch (e) {}

      try {
        const compRes = await fetch('/api/comparison');
        if (compRes.ok) setComparison(await compRes.json());
      } catch (e) {}
    };

    fetchData();
    const interval = setInterval(fetchData, 3000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div style={{ display: 'flex', minHeight: '100vh', background: '#F4F5F9' }}>
      {/* Jobgio-styled Sidebar */}
      <Sidebar activeTab={activeTab} setActiveTab={setActiveTab} />

      {/* Main Content Area */}
      <main style={{ flex: 1, padding: '28px 36px', overflowY: 'auto' }}>
        {/* Top Quick Stats Pill Row */}
        <QuickStatsHeader stats={stats} />

        {/* Panel View Switcher */}
        {activeTab === 'dashboard' && <LiveMonitoringPanel telemetryData={telemetry} />}
        {activeTab === 'failures' && <FailureVizPanel failureData={failures} />}
        {activeTab === 'constraints' && <ConstraintExplorerPanel constraints={constraints} />}
        {activeTab === 'experiments' && <ExperimentTrackerPanel experimentData={experiments} />}
        {activeTab === 'z3' && <Z3ResultsPanel z3Data={z3Logs} />}
        {activeTab === 'policy' && <PolicyRecommendationsPanel />}
        {activeTab === 'comparison' && <BeforeAfterPanel comparisonData={comparison} />}
      </main>
    </div>
  );
}
