import React, { useEffect, useState } from 'react';
import {
  Search,
  Filter,
  RotateCw,
  Play,
  Activity,
  AlertCircle,
  Database,
  ArrowRight,
  Clock,
  GitBranch,
  ShieldAlert,
  Copy,
  Check,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
} from 'lucide-react';
import { api, ApiError } from '../services/api';
import { ExecutionListResponse, ExecutionStatus, ExecutionSummaryResponse } from '../types/api';
import { formatDateTime, formatDuration, formatShortId, getStatusBadgeClass } from '../utils/format';

interface ExecutionsPageProps {
  onSelectExecutionForTrace: (executionId: string) => void;
  onSelectExecutionForDiagnosis: (executionId: string) => void;
  onOpenRunModal: () => void;
}

export const ExecutionsPage: React.FC<ExecutionsPageProps> = ({
  onSelectExecutionForTrace,
  onSelectExecutionForDiagnosis,
  onOpenRunModal,
}) => {
  const [data, setData] = useState<ExecutionListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isOfflinePersistence, setIsOfflinePersistence] = useState(false);

  // Filters & Pagination
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [page, setPage] = useState(0);
  const limit = 15;

  // Selected execution detail drawer/card
  const [selectedExecution, setSelectedExecution] = useState<ExecutionSummaryResponse | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const fetchExecutions = async () => {
    setLoading(true);
    setError(null);
    setIsOfflinePersistence(false);
    try {
      const resp = await api.listExecutions({
        limit,
        offset: page * limit,
        status: statusFilter || undefined,
        query: search.trim() || undefined,
      });
      setData(resp);
    } catch (err: any) {
      if (err instanceof ApiError && err.code === 'persistence_not_configured') {
        setIsOfflinePersistence(true);
      } else if (err instanceof ApiError) {
        setError(`[${err.code || err.status}] ${err.detail}`);
      } else {
        setError(err.message || 'Failed to load executions');
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchExecutions();
  }, [page, statusFilter]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(0);
    fetchExecutions();
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(text);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const totalPages = data ? Math.ceil(data.total / limit) : 0;

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
            Execution History
          </h1>
          <p className="text-xs text-slate-400 mt-1">
            Persisted execution runs with durations, status breakdowns, and trace access
          </p>
        </div>
        <div className="flex items-center gap-2.5">
          <button
            onClick={() => fetchExecutions()}
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
            <span>Execute Agent</span>
          </button>
        </div>
      </div>

      {/* Offline Persistence Alert */}
      {isOfflinePersistence && (
        <div className="p-4 rounded-xl bg-amber-950/30 border border-amber-800/50 flex items-start gap-3 text-xs">
          <Database size={18} className="text-amber-400 shrink-0 mt-0.5" />
          <div className="space-y-1">
            <div className="font-semibold text-amber-200">Execution Store Not Configured</div>
            <p className="text-slate-300 leading-relaxed">
              Execution history requires PostgreSQL persistence. Launch the application with{' '}
              <code className="text-amber-300 font-mono">AGENTGUARD_DATABASE_URL</code> configured to persist telemetry traces.
              You can still execute jobs in memory using the <span className="text-white font-medium">Execute Agent</span> action above.
            </p>
          </div>
        </div>
      )}

      {/* Error Banner */}
      {error && !isOfflinePersistence && (
        <div className="p-4 rounded-xl bg-rose-950/40 border border-rose-800/60 flex items-start gap-3 text-xs text-rose-300">
          <AlertCircle size={18} className="text-rose-400 shrink-0 mt-0.5" />
          <div>
            <div className="font-semibold text-rose-200">Error Fetching Executions</div>
            <div className="font-mono text-[11px] mt-0.5">{error}</div>
          </div>
        </div>
      )}

      {/* Filters Bar */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 bg-surface p-3 rounded-xl border border-surface-border">
        {/* Search */}
        <form onSubmit={handleSearchSubmit} className="flex-1 relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            type="text"
            placeholder="Search by execution ID, task ID, or task text..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-9 pr-3 py-1.5 bg-surface-elevated border border-surface-border rounded-lg text-white placeholder-slate-500 text-xs focus:outline-none focus:ring-1 focus:ring-brand-500"
          />
        </form>

        {/* Status Filter */}
        <div className="flex items-center gap-2">
          <Filter size={14} className="text-slate-500 shrink-0" />
          <select
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value);
              setPage(0);
            }}
            className="px-2.5 py-1.5 bg-surface-elevated border border-surface-border rounded-lg text-slate-300 text-xs focus:outline-none focus:ring-1 focus:ring-brand-500"
          >
            <option value="">All Statuses</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="partially_completed">Partially Completed</option>
            <option value="running">Running</option>
          </select>
        </div>
      </div>

      {/* Executions Table */}
      <div className="bg-surface rounded-xl border border-surface-border overflow-hidden shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-surface-elevated/70 border-b border-surface-border text-slate-400 uppercase font-semibold text-[10px] tracking-wider select-none">
              <tr>
                <th className="py-3 px-4">Execution ID</th>
                <th className="py-3 px-4">Task Description</th>
                <th className="py-3 px-4">Status</th>
                <th className="py-3 px-4">Mode</th>
                <th className="py-3 px-4">Duration</th>
                <th className="py-3 px-4">Tools (Ok/Fail)</th>
                <th className="py-3 px-4">Timestamp</th>
                <th className="py-3 px-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-surface-border font-sans">
              {loading && !data ? (
                <tr>
                  <td colSpan={8} className="py-12 text-center text-slate-500">
                    <div className="flex flex-col items-center justify-center gap-2">
                      <div className="w-6 h-6 border-2 border-brand-500 border-t-transparent rounded-full animate-spin" />
                      <span>Loading execution records...</span>
                    </div>
                  </td>
                </tr>
              ) : data && data.items.length > 0 ? (
                data.items.map((exec) => {
                  const isSelected = selectedExecution?.execution_id === exec.execution_id;
                  return (
                    <tr
                      key={exec.execution_id}
                      onClick={() => setSelectedExecution(exec)}
                      className={`hover:bg-surface-elevated/50 cursor-pointer transition-colors ${
                        isSelected ? 'bg-surface-elevated/80' : ''
                      }`}
                    >
                      {/* Execution ID */}
                      <td className="py-3 px-4 font-mono font-medium text-slate-300">
                        <div className="flex items-center gap-1.5">
                          <span>{formatShortId(exec.execution_id)}</span>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              copyToClipboard(exec.execution_id);
                            }}
                            className="text-slate-500 hover:text-slate-300 p-0.5 rounded transition-colors"
                            title="Copy Execution ID"
                          >
                            {copiedId === exec.execution_id ? (
                              <Check size={11} className="text-emerald-400" />
                            ) : (
                              <Copy size={11} />
                            )}
                          </button>
                        </div>
                      </td>

                      {/* Task text */}
                      <td className="py-3 px-4 max-w-xs truncate text-slate-200">
                        {exec.task_description || (
                          <span className="text-slate-500 italic">No task text stored</span>
                        )}
                      </td>

                      {/* Status */}
                      <td className="py-3 px-4">
                        <span
                          className={`inline-flex px-2 py-0.5 rounded-full border text-[10px] font-mono font-medium capitalize ${getStatusBadgeClass(
                            exec.status
                          )}`}
                        >
                          {exec.status.replace('_', ' ')}
                        </span>
                      </td>

                      {/* Mode */}
                      <td className="py-3 px-4 font-mono text-[11px] text-slate-400 capitalize">
                        {exec.mode}
                      </td>

                      {/* Duration */}
                      <td className="py-3 px-4 font-mono text-[11px] text-slate-300">
                        {formatDuration(exec.duration_ms)}
                      </td>

                      {/* Tools Count */}
                      <td className="py-3 px-4 font-mono text-[11px]">
                        <span className="text-emerald-400">{exec.successful_tool_call_count}</span>
                        <span className="text-slate-600"> / </span>
                        <span className={exec.failed_tool_call_count > 0 ? 'text-rose-400 font-medium' : 'text-slate-500'}>
                          {exec.failed_tool_call_count}
                        </span>
                      </td>

                      {/* Timestamp */}
                      <td className="py-3 px-4 font-mono text-[11px] text-slate-400">
                        {formatDateTime(exec.started_at)}
                      </td>

                      {/* Actions */}
                      <td className="py-3 px-4 text-right" onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center justify-end gap-1.5">
                          <button
                            onClick={() => onSelectExecutionForTrace(exec.execution_id)}
                            className="p-1 rounded hover:bg-surface-elevated text-slate-400 hover:text-brand-400 transition-colors"
                            title="Explore Trace"
                          >
                            <GitBranch size={14} />
                          </button>
                          <button
                            onClick={() => onSelectExecutionForDiagnosis(exec.execution_id)}
                            className="p-1 rounded hover:bg-surface-elevated text-slate-400 hover:text-amber-400 transition-colors"
                            title="Diagnose Failure"
                          >
                            <ShieldAlert size={14} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })
              ) : (
                <tr>
                  <td colSpan={8} className="py-12 text-center text-slate-500">
                    <Activity size={24} className="mx-auto mb-2 opacity-40" />
                    <div>No executions found matching the current criteria.</div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination Footer */}
        {data && data.total > 0 && (
          <div className="p-3 bg-surface-elevated/40 border-t border-surface-border flex items-center justify-between text-xs text-slate-400 select-none">
            <div className="font-mono text-[11px]">
              Showing <span className="text-white font-medium">{page * limit + 1}</span> to{' '}
              <span className="text-white font-medium">{Math.min((page + 1) * limit, data.total)}</span> of{' '}
              <span className="text-white font-medium">{data.total}</span> records
            </div>

            <div className="flex items-center gap-1.5">
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className="p-1.5 rounded-lg border border-surface-border bg-surface-elevated hover:bg-surface disabled:opacity-40 text-slate-300 transition-colors"
              >
                <ChevronLeft size={14} />
              </button>
              <span className="px-2 font-mono text-[11px]">
                Page {page + 1} of {Math.max(1, totalPages)}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                disabled={page >= totalPages - 1}
                className="p-1.5 rounded-lg border border-surface-border bg-surface-elevated hover:bg-surface disabled:opacity-40 text-slate-300 transition-colors"
              >
                <ChevronRight size={14} />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Selected Execution Quick Detail Panel */}
      {selectedExecution && (
        <div className="p-4 rounded-xl bg-surface border border-brand-500/30 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-white uppercase tracking-wider">
                Execution Detail
              </span>
              <span className="font-mono text-xs text-brand-400">{selectedExecution.execution_id}</span>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => onSelectExecutionForTrace(selectedExecution.execution_id)}
                className="px-2.5 py-1 rounded-md bg-brand-600 hover:bg-brand-500 text-white text-xs font-medium flex items-center gap-1.5 transition-colors"
              >
                <GitBranch size={13} />
                <span>Open in Trace Explorer</span>
              </button>
              <button
                onClick={() => onSelectExecutionForDiagnosis(selectedExecution.execution_id)}
                className="px-2.5 py-1 rounded-md bg-surface-elevated hover:bg-surface border border-surface-border text-amber-400 text-xs font-medium flex items-center gap-1.5 transition-colors"
              >
                <ShieldAlert size={13} />
                <span>Run Intelligence</span>
              </button>
            </div>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 bg-surface-elevated/70 p-3 rounded-lg text-xs font-mono">
            <div>
              <span className="text-slate-500 block text-[10px] uppercase">Status</span>
              <span className="text-white capitalize font-medium">{selectedExecution.status}</span>
            </div>
            <div>
              <span className="text-slate-500 block text-[10px] uppercase">Duration</span>
              <span className="text-white font-medium">{formatDuration(selectedExecution.duration_ms)}</span>
            </div>
            <div>
              <span className="text-slate-500 block text-[10px] uppercase">Tool Calls</span>
              <span className="text-emerald-400 font-medium">{selectedExecution.successful_tool_call_count} ok</span>
              <span className="text-slate-600"> / </span>
              <span className="text-rose-400 font-medium">{selectedExecution.failed_tool_call_count} fail</span>
            </div>
            <div>
              <span className="text-slate-500 block text-[10px] uppercase">Started At</span>
              <span className="text-slate-300">{formatDateTime(selectedExecution.started_at)}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
