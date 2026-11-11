import React, { useEffect, useState } from 'react';
import {
  GitBranch,
  Search,
  RotateCw,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Clock,
  Wrench,
  ChevronDown,
  ChevronRight,
  ShieldAlert,
  ArrowRight,
  Copy,
  Check,
  Terminal,
  Database,
  Filter,
} from 'lucide-react';
import { api, ApiError } from '../services/api';
import {
  ExecutionEventResponse,
  ExecutionSummaryResponse,
} from '../types/api';
import {
  formatDateTime,
  formatDuration,
  formatShortId,
  getStatusBadgeClass,
} from '../utils/format';

interface TraceExplorerPageProps {
  initialExecutionId?: string | null;
  onNavigateToDiagnosis: (executionId: string) => void;
}

export const TraceExplorerPage: React.FC<TraceExplorerPageProps> = ({
  initialExecutionId,
  onNavigateToDiagnosis,
}) => {
  const [executionIdInput, setExecutionIdInput] = useState(initialExecutionId || '');
  const [activeExecutionId, setActiveExecutionId] = useState<string | null>(initialExecutionId || null);

  const [summary, setSummary] = useState<ExecutionSummaryResponse | null>(null);
  const [events, setEvents] = useState<ExecutionEventResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isOfflinePersistence, setIsOfflinePersistence] = useState(false);

  // Filter events in UI
  const [componentFilter, setComponentFilter] = useState<string>('all');
  const [expandedEventId, setExpandedEventId] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // Pagination cursor
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    if (initialExecutionId) {
      setExecutionIdInput(initialExecutionId);
      setActiveExecutionId(initialExecutionId);
      loadTrace(initialExecutionId);
    }
  }, [initialExecutionId]);

  const loadTrace = async (id: string) => {
    if (!id.trim()) return;
    setLoading(true);
    setError(null);
    setIsOfflinePersistence(false);
    setEvents([]);
    setSummary(null);

    try {
      // 1. Fetch summary
      const sum = await api.getExecution(id.trim());
      setSummary(sum);

      // 2. Fetch events
      const page = await api.listEvents(id.trim(), 100);
      setEvents(page.events);
      setNextCursor(page.next_after_event_index ?? null);
      setActiveExecutionId(id.trim());
    } catch (err: any) {
      if (err instanceof ApiError && (err.code === 'persistence_not_configured' || err.code === 'persistence_unavailable')) {
        setIsOfflinePersistence(true);
      } else if (err instanceof ApiError && err.status === 404) {
        setError(`Execution ${id} was not found in persistence store.`);
      } else if (err instanceof ApiError) {
        setError(`[${err.code || err.status}] ${err.detail}`);
      } else {
        setError(err.message || 'Failed to load trace events');
      }
    } finally {
      setLoading(false);
    }
  };

  const loadMoreEvents = async () => {
    if (!activeExecutionId || nextCursor === null || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await api.listEvents(activeExecutionId, 100, nextCursor);
      setEvents((prev) => [...prev, ...page.events]);
      setNextCursor(page.next_after_event_index ?? null);
    } catch (err: any) {
      console.error('Failed to load next event page', err);
    } finally {
      setLoadingMore(false);
    }
  };

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (executionIdInput.trim()) {
      loadTrace(executionIdInput.trim());
    }
  };

  const filteredEvents = events.filter((evt) => {
    if (componentFilter === 'tool') return evt.component === 'tool';
    if (componentFilter === 'agent') return evt.component === 'agent';
    if (componentFilter === 'errors') return evt.status === 'failed' || evt.error_type;
    return true;
  });

  const copyId = () => {
    if (activeExecutionId) {
      navigator.clipboard.writeText(activeExecutionId);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
            Trace Explorer
          </h1>
          <p className="text-xs text-slate-400 mt-1">
            Ordered telemetry event stream, step timelines, tool calls, and error diagnostics
          </p>
        </div>

        {activeExecutionId && (
          <button
            onClick={() => onNavigateToDiagnosis(activeExecutionId)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-500/10 hover:bg-amber-500/20 border border-amber-500/30 text-amber-300 text-xs font-medium transition-colors"
          >
            <ShieldAlert size={14} />
            <span>Run Failure Intelligence</span>
          </button>
        )}
      </div>

      {/* Execution ID Search Bar */}
      <form onSubmit={handleSearchSubmit} className="flex gap-2">
        <div className="relative flex-1">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            type="text"
            placeholder="Enter UUID of persisted execution (e.g. 7f3ef2a5-...)"
            value={executionIdInput}
            onChange={(e) => setExecutionIdInput(e.target.value)}
            className="w-full pl-9 pr-3 py-2 bg-surface border border-surface-border rounded-lg text-white placeholder-slate-500 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-brand-500"
          />
        </div>
        <button
          type="submit"
          disabled={loading || !executionIdInput.trim()}
          className="px-4 py-2 bg-brand-600 hover:bg-brand-500 disabled:opacity-50 text-white rounded-lg text-xs font-medium transition-colors flex items-center gap-1.5 shrink-0"
        >
          {loading ? <RotateCw size={14} className="animate-spin" /> : <GitBranch size={14} />}
          <span>Inspect Trace</span>
        </button>
      </form>

      {/* Offline Persistence Alert */}
      {isOfflinePersistence && (
        <div className="p-4 rounded-xl bg-amber-950/30 border border-amber-800/50 flex items-start gap-3 text-xs">
          <Database size={18} className="text-amber-400 shrink-0 mt-0.5" />
          <div className="space-y-1">
            <div className="font-semibold text-amber-200">Trace Store Unavailable</div>
            <p className="text-slate-300 leading-relaxed">
              Trace lookup requires PostgreSQL telemetry persistence. When database persistence is disabled,
              traces are not recorded in the store. Enable <code className="text-amber-300 font-mono">AGENTGUARD_DATABASE_URL</code> in your environment.
            </p>
          </div>
        </div>
      )}

      {/* Error Message */}
      {error && !isOfflinePersistence && (
        <div className="p-4 rounded-xl bg-rose-950/40 border border-rose-800/60 flex items-start gap-3 text-xs text-rose-300">
          <AlertTriangle size={18} className="text-rose-400 shrink-0 mt-0.5" />
          <div>
            <div className="font-semibold text-rose-200">Trace Retrieval Error</div>
            <div className="font-mono text-[11px] mt-0.5">{error}</div>
          </div>
        </div>
      )}

      {/* Execution Summary Header Banner */}
      {summary && (
        <div className="p-4 rounded-xl bg-surface border border-surface-border space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <span className="font-mono text-xs text-white font-medium">
                {summary.execution_id}
              </span>
              <button
                onClick={copyId}
                className="text-slate-500 hover:text-slate-300 p-0.5 transition-colors"
                title="Copy Execution ID"
              >
                {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
              </button>
            </div>
            <div className="flex items-center gap-2">
              <span className={`px-2 py-0.5 rounded-full border text-[10px] font-mono capitalize ${getStatusBadgeClass(summary.status)}`}>
                {summary.status.replace('_', ' ')}
              </span>
              <span className="px-2 py-0.5 rounded-full border border-surface-border bg-surface-elevated text-[10px] font-mono text-slate-300 capitalize">
                {summary.mode} mode
              </span>
            </div>
          </div>

          {summary.task_description && (
            <div className="text-xs text-slate-300 font-sans p-2 rounded bg-surface-elevated border border-surface-border/60">
              <span className="text-slate-500 font-mono text-[10px] uppercase block mb-0.5">Task Description</span>
              {summary.task_description}
            </div>
          )}

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-[11px] font-mono pt-1 text-slate-400">
            <div>
              <span className="text-slate-500 block text-[10px]">DURATION</span>
              <span className="text-slate-200 font-medium">{formatDuration(summary.duration_ms)}</span>
            </div>
            <div>
              <span className="text-slate-500 block text-[10px]">TOOL CALLS</span>
              <span className="text-emerald-400 font-medium">{summary.successful_tool_call_count} ok</span>
              {' / '}
              <span className="text-rose-400 font-medium">{summary.failed_tool_call_count} failed</span>
            </div>
            <div>
              <span className="text-slate-500 block text-[10px]">STARTED AT</span>
              <span className="text-slate-300">{formatDateTime(summary.started_at)}</span>
            </div>
            <div>
              <span className="text-slate-500 block text-[10px]">COMPLETED AT</span>
              <span className="text-slate-300">{formatDateTime(summary.completed_at)}</span>
            </div>
          </div>
        </div>
      )}

      {/* Trace Timeline */}
      {summary && (
        <div className="space-y-3">
          {/* Timeline Filter Controls */}
          <div className="flex items-center justify-between bg-surface p-2.5 rounded-xl border border-surface-border text-xs">
            <div className="flex items-center gap-2">
              <span className="text-slate-400 font-medium text-xs">Filter Events:</span>
              <div className="flex gap-1">
                {(['all', 'tool', 'agent', 'errors'] as const).map((filter) => (
                  <button
                    key={filter}
                    onClick={() => setComponentFilter(filter)}
                    className={`px-2.5 py-1 rounded text-[11px] font-medium capitalize transition-all ${
                      componentFilter === filter
                        ? 'bg-brand-500/20 text-brand-300 border border-brand-500/40'
                        : 'text-slate-400 hover:text-slate-200 border border-transparent'
                    }`}
                  >
                    {filter}
                  </button>
                ))}
              </div>
            </div>

            <span className="text-slate-500 font-mono text-[11px]">
              {filteredEvents.length} events
            </span>
          </div>

          {/* Events Stream List */}
          <div className="space-y-2">
            {filteredEvents.map((evt) => {
              const isExpanded = expandedEventId === evt.event_id;
              const isError = evt.status === 'failed' || evt.error_type;

              return (
                <div
                  key={evt.event_id}
                  className={`rounded-xl border transition-all text-xs font-sans overflow-hidden ${
                    isError
                      ? 'bg-rose-950/20 border-rose-800/40'
                      : 'bg-surface border-surface-border hover:border-surface-border/80'
                  }`}
                >
                  {/* Event Summary Bar */}
                  <div
                    onClick={() => setExpandedEventId(isExpanded ? null : evt.event_id)}
                    className="p-3 flex items-center justify-between cursor-pointer select-none"
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <span className="font-mono text-[10px] text-slate-500 w-8 text-right">
                        #{evt.event_index}
                      </span>

                      {/* Status Icon */}
                      {evt.status === 'completed' ? (
                        <CheckCircle2 size={15} className="text-emerald-400 shrink-0" />
                      ) : evt.status === 'failed' ? (
                        <XCircle size={15} className="text-rose-400 shrink-0" />
                      ) : (
                        <Clock size={15} className="text-blue-400 shrink-0" />
                      )}

                      {/* Event Type & Tool Badge */}
                      <div className="flex items-center gap-2 truncate">
                        <span className="font-mono font-medium text-white text-[11px]">
                          {evt.event_type}
                        </span>
                        {evt.tool_name && (
                          <span className="px-2 py-0.5 rounded bg-brand-500/10 border border-brand-500/20 text-brand-300 font-mono text-[10px]">
                            {evt.tool_name}
                          </span>
                        )}
                        <span className="text-[10px] text-slate-500 font-mono uppercase">
                          [{evt.component}]
                        </span>
                      </div>
                    </div>

                    <div className="flex items-center gap-3 shrink-0 font-mono text-[11px] text-slate-400">
                      {evt.duration_ms !== null && evt.duration_ms !== undefined && (
                        <span>{formatDuration(evt.duration_ms)}</span>
                      )}
                      <span>{formatDateTime(evt.timestamp)}</span>
                      {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    </div>
                  </div>

                  {/* Expanded Event Details */}
                  {isExpanded && (
                    <div className="p-3 bg-surface-elevated/80 border-t border-surface-border space-y-2 text-xs font-mono">
                      <div className="grid grid-cols-2 gap-2 text-[11px]">
                        <div>
                          <span className="text-slate-500">Event ID: </span>
                          <span className="text-slate-300">{evt.event_id}</span>
                        </div>
                        <div>
                          <span className="text-slate-500">Step: </span>
                          <span className="text-slate-300">{evt.step_number}</span>
                        </div>
                      </div>

                      {/* Error Info if failed */}
                      {evt.error_type && (
                        <div className="p-2 rounded bg-rose-950/40 border border-rose-800/60 text-rose-300 space-y-1">
                          <div className="font-semibold text-[11px]">Error: {evt.error_type}</div>
                          {evt.error_message && (
                            <div className="text-[11px] text-rose-200/90 font-sans">{evt.error_message}</div>
                          )}
                        </div>
                      )}

                      {/* Metadata Viewer */}
                      {evt.metadata && Object.keys(evt.metadata).length > 0 && (
                        <div>
                          <span className="text-slate-500 block text-[10px] uppercase mb-1">Metadata</span>
                          <pre className="p-2 rounded bg-background border border-surface-border text-[11px] text-slate-300 overflow-x-auto">
                            {JSON.stringify(evt.metadata, null, 2)}
                          </pre>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Load More Button if nextCursor exists */}
          {nextCursor !== null && (
            <div className="pt-2 text-center">
              <button
                onClick={loadMoreEvents}
                disabled={loadingMore}
                className="px-4 py-2 rounded-lg bg-surface-elevated hover:bg-surface border border-surface-border text-xs text-brand-300 font-medium transition-colors"
              >
                {loadingMore ? 'Loading next page...' : 'Load More Events'}
              </button>
            </div>
          )}
        </div>
      )}

      {/* Empty Initial State */}
      {!summary && !loading && !error && !isOfflinePersistence && (
        <div className="p-12 text-center bg-surface rounded-xl border border-surface-border text-slate-500 space-y-3">
          <Terminal size={32} className="mx-auto opacity-40 text-brand-400" />
          <div className="text-sm font-medium text-slate-300">No Trace Selected</div>
          <p className="text-xs max-w-sm mx-auto">
            Enter an execution ID in the search bar above or choose one from the Execution History page to explore its ordered event timeline.
          </p>
        </div>
      )}
    </div>
  );
};
