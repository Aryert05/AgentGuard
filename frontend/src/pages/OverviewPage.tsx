import React, { useEffect, useState } from 'react';
import {
  Activity,
  CheckCircle2,
  XCircle,
  Clock,
  Wrench,
  TrendingUp,
  AlertTriangle,
  Play,
  RotateCw,
  ArrowRight,
  Database,
  ExternalLink,
} from 'lucide-react';
import {
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Tooltip,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  AreaChart,
  Area,
} from 'recharts';
import { api, ApiError } from '../services/api';
import { OverviewMetricsResponse } from '../types/api';
import { formatDuration } from '../utils/format';

interface OverviewPageProps {
  onNavigateToExecutions: () => void;
  onNavigateToTrace: (id?: string) => void;
  onNavigateToDiagnosis: (id?: string) => void;
  onOpenRunModal: () => void;
  persistenceStatus: 'configured' | 'unconfigured';
}

const OUTCOME_COLORS: Record<string, string> = {
  completed: '#10B981', // Emerald
  failed: '#EF4444', // Rose
  partially_completed: '#F59E0B', // Amber
  running: '#3B82F6', // Blue
  pending: '#6B7280', // Slate
};

export const OverviewPage: React.FC<OverviewPageProps> = ({
  onNavigateToExecutions,
  onNavigateToTrace,
  onNavigateToDiagnosis,
  onOpenRunModal,
  persistenceStatus,
}) => {
  const [metrics, setMetrics] = useState<OverviewMetricsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isOfflinePersistence, setIsOfflinePersistence] = useState(false);

  const fetchMetrics = async () => {
    setLoading(true);
    setError(null);
    setIsOfflinePersistence(false);
    try {
      const data = await api.getOverview();
      setMetrics(data);
    } catch (err: any) {
      if (err instanceof ApiError && err.code === 'persistence_not_configured') {
        setIsOfflinePersistence(true);
      } else if (err instanceof ApiError) {
        setError(`[${err.code || err.status}] ${err.detail}`);
      } else {
        setError(err.message || 'Failed to load telemetry overview');
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMetrics();
  }, []);

  // Format data for Recharts Pie chart
  const outcomeData = metrics?.status_distribution
    ? Object.entries(metrics.status_distribution).map(([status, count]) => ({
        name: status.replace('_', ' '),
        value: count,
        color: OUTCOME_COLORS[status] || '#8B5CF6',
      }))
    : [];

  // Format failure categories
  const failureCategoryData = metrics?.failure_category_distribution
    ? Object.entries(metrics.failure_category_distribution).map(([category, count]) => ({
        category: category.replace(/_/g, ' '),
        count,
      }))
    : [];

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Page Title & Controls */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
            System Overview
          </h1>
          <p className="text-xs text-slate-400 mt-1">
            Real-time observability and reliability telemetry from execution traces
          </p>
        </div>
        <div className="flex items-center gap-2.5">
          <button
            onClick={fetchMetrics}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-elevated hover:bg-surface border border-surface-border text-slate-300 text-xs font-medium transition-colors"
          >
            <RotateCw size={13} className={loading ? 'animate-spin' : ''} />
            <span>Refresh</span>
          </button>
          <button
            onClick={onOpenRunModal}
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-brand-600 hover:bg-brand-500 text-white text-xs font-medium transition-colors shadow-sm"
          >
            <Play size={13} className="fill-current" />
            <span>Run Agent</span>
          </button>
        </div>
      </div>

      {/* Persistence Notice (Offline Mode Banner) */}
      {isOfflinePersistence && (
        <div className="p-4 rounded-xl bg-amber-950/30 border border-amber-800/50 flex items-start gap-3 text-xs">
          <Database size={18} className="text-amber-400 shrink-0 mt-0.5" />
          <div className="space-y-1">
            <div className="font-semibold text-amber-200">Database Persistence Is Not Configured</div>
            <p className="text-slate-300 leading-relaxed">
              AgentGuard is running in <span className="font-semibold text-white">offline deterministic mode</span>.
              Aggregated historical metrics and PostgreSQL trace queries require{' '}
              <code className="text-amber-300 font-mono">AGENTGUARD_DATABASE_URL</code> to be configured in your environment.
              You can still execute agents synchronously and run stateless failure diagnoses.
            </p>
          </div>
        </div>
      )}

      {/* General Error Banner */}
      {error && !isOfflinePersistence && (
        <div className="p-4 rounded-xl bg-rose-950/40 border border-rose-800/60 flex items-start gap-3 text-xs text-rose-300">
          <AlertTriangle size={18} className="text-rose-400 shrink-0 mt-0.5" />
          <div>
            <div className="font-semibold text-rose-200">Telemetry Feed Unavailable</div>
            <div className="font-mono text-[11px] mt-0.5">{error}</div>
          </div>
        </div>
      )}

      {/* KPI Stat Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {/* Total Executions */}
        <div className="p-4 rounded-xl bg-surface border border-surface-border shadow-sm">
          <div className="flex items-center justify-between text-slate-400 mb-2">
            <span className="text-xs font-medium">Total Executions</span>
            <Activity size={16} className="text-brand-400" />
          </div>
          <div className="text-2xl font-bold font-mono text-white">
            {metrics ? metrics.total_executions.toLocaleString() : '—'}
          </div>
          <div className="text-[11px] text-slate-500 mt-1 flex items-center gap-1 font-mono">
            <span>{metrics?.status_distribution.completed || 0} completed</span>
            <span>•</span>
            <span>{metrics?.status_distribution.failed || 0} failed</span>
          </div>
        </div>

        {/* Success Rate */}
        <div className="p-4 rounded-xl bg-surface border border-surface-border shadow-sm">
          <div className="flex items-center justify-between text-slate-400 mb-2">
            <span className="text-xs font-medium">Success Rate</span>
            <CheckCircle2 size={16} className="text-emerald-400" />
          </div>
          <div className="text-2xl font-bold font-mono text-white">
            {metrics ? `${(metrics.success_rate * 100).toFixed(1)}%` : '—'}
          </div>
          <div className="text-[11px] text-emerald-400/80 mt-1 flex items-center gap-1 font-mono">
            <TrendingUp size={12} />
            <span>{metrics?.successful_executions || 0} clean executions</span>
          </div>
        </div>

        {/* Failure Rate */}
        <div className="p-4 rounded-xl bg-surface border border-surface-border shadow-sm">
          <div className="flex items-center justify-between text-slate-400 mb-2">
            <span className="text-xs font-medium">Failure Rate</span>
            <XCircle size={16} className="text-rose-400" />
          </div>
          <div className="text-2xl font-bold font-mono text-white">
            {metrics ? `${(metrics.failure_rate * 100).toFixed(1)}%` : '—'}
          </div>
          <div className="text-[11px] text-rose-400/80 mt-1 font-mono">
            <span>{metrics ? metrics.failed_executions + metrics.partially_completed_executions : 0} interrupted</span>
          </div>
        </div>

        {/* Avg Duration */}
        <div className="p-4 rounded-xl bg-surface border border-surface-border shadow-sm">
          <div className="flex items-center justify-between text-slate-400 mb-2">
            <span className="text-xs font-medium">Avg Duration</span>
            <Clock size={16} className="text-blue-400" />
          </div>
          <div className="text-2xl font-bold font-mono text-white">
            {metrics?.avg_duration_ms ? formatDuration(metrics.avg_duration_ms) : '—'}
          </div>
          <div className="text-[11px] text-slate-500 mt-1 flex items-center gap-1 font-mono">
            <Wrench size={12} />
            <span>{metrics?.total_tool_calls || 0} tool calls recorded</span>
          </div>
        </div>
      </div>

      {/* Charts Section */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Outcome Breakdown Chart */}
        <div className="p-5 rounded-xl bg-surface border border-surface-border flex flex-col">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-xs font-semibold text-white uppercase tracking-wider">
              Execution Outcomes
            </h3>
            <span className="text-[11px] text-slate-500 font-mono">Status Distribution</span>
          </div>

          <div className="h-56 flex items-center justify-center">
            {outcomeData.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={outcomeData}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    innerRadius={50}
                    outerRadius={80}
                    paddingAngle={3}
                  >
                    {outcomeData.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={entry.color} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#111827',
                      borderColor: '#232F45',
                      borderRadius: '8px',
                      fontSize: '11px',
                      color: '#F3F4F6',
                    }}
                  />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <div className="text-center text-slate-500 text-xs">
                {isOfflinePersistence ? 'Persistence disabled' : 'No execution data available yet'}
              </div>
            )}
          </div>

          {/* Legend */}
          <div className="mt-2 flex flex-wrap gap-3 justify-center text-[11px] font-mono">
            {outcomeData.map((d) => (
              <div key={d.name} className="flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: d.color }} />
                <span className="text-slate-400 capitalize">{d.name}:</span>
                <span className="text-white font-medium">{d.value}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Recent Execution Activity Timeline */}
        <div className="p-5 rounded-xl bg-surface border border-surface-border lg:col-span-2 flex flex-col">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-xs font-semibold text-white uppercase tracking-wider">
                Execution Activity Trend
              </h3>
              <p className="text-[11px] text-slate-400 mt-0.5">Chronological trace volume by day</p>
            </div>
            <button
              onClick={onNavigateToExecutions}
              className="text-xs text-brand-400 hover:text-brand-300 flex items-center gap-1 font-medium transition-colors"
            >
              <span>View History</span>
              <ArrowRight size={12} />
            </button>
          </div>

          <div className="h-56">
            {metrics?.recent_activity && metrics.recent_activity.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={metrics.recent_activity}>
                  <defs>
                    <linearGradient id="colorTotal" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#3B82F6" stopOpacity={0.4} />
                      <stop offset="95%" stopColor="#3B82F6" stopOpacity={0} />
                    </linearGradient>
                    <linearGradient id="colorSuccess" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#10B981" stopOpacity={0.4} />
                      <stop offset="95%" stopColor="#10B981" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1F2937" vertical={false} />
                  <XAxis dataKey="date" stroke="#6B7280" fontSize={10} tickLine={false} />
                  <YAxis stroke="#6B7280" fontSize={10} tickLine={false} allowDecimals={false} />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#111827',
                      borderColor: '#232F45',
                      borderRadius: '8px',
                      fontSize: '11px',
                      color: '#F3F4F6',
                    }}
                  />
                  <Area type="monotone" dataKey="total" name="Total Runs" stroke="#3B82F6" fillOpacity={1} fill="url(#colorTotal)" />
                  <Area type="monotone" dataKey="successful" name="Successes" stroke="#10B981" fillOpacity={1} fill="url(#colorSuccess)" />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <div className="h-full flex items-center justify-center text-center text-slate-500 text-xs">
                {isOfflinePersistence
                  ? 'Historical activity requires database persistence'
                  : 'Execute tasks to observe time-series telemetry trends'}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Failure Category Distribution Bar Chart */}
      {failureCategoryData.length > 0 && (
        <div className="p-5 rounded-xl bg-surface border border-surface-border">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-xs font-semibold text-white uppercase tracking-wider">
                Observed Failure Categories
              </h3>
              <p className="text-[11px] text-slate-400 mt-0.5">
                Distribution of deterministic failure classifications identified in traces
              </p>
            </div>
            <button
              onClick={() => onNavigateToDiagnosis()}
              className="text-xs text-brand-400 hover:text-brand-300 flex items-center gap-1 font-medium transition-colors"
            >
              <span>Explore Intelligence</span>
              <ArrowRight size={12} />
            </button>
          </div>

          <div className="h-44">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={failureCategoryData} layout="vertical" margin={{ left: 40, right: 20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1F2937" horizontal={false} />
                <XAxis type="number" stroke="#6B7280" fontSize={10} allowDecimals={false} />
                <YAxis dataKey="category" type="category" stroke="#9CA3AF" fontSize={10} tickLine={false} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#111827',
                    borderColor: '#232F45',
                    borderRadius: '8px',
                    fontSize: '11px',
                    color: '#F3F4F6',
                  }}
                />
                <Bar dataKey="count" name="Failure Events" fill="#F43F5E" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </div>
  );
};
