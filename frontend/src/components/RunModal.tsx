import React, { useState } from 'react';
import { X, Play, Loader2, CheckCircle2, AlertCircle, ArrowRight, ShieldAlert, GitBranch } from 'lucide-react';
import { api, ApiError } from '../services/api';
import { ExecutionMode, ExecutionResponse } from '../types/api';
import { formatDuration, getStatusBadgeClass } from '../utils/format';

interface RunModalProps {
  isOpen: boolean;
  onClose: () => void;
  onNavigateToTrace: (executionId: string) => void;
  onNavigateToDiagnosis: (executionId: string) => void;
}

export const RunModal: React.FC<RunModalProps> = ({
  isOpen,
  onClose,
  onNavigateToTrace,
  onNavigateToDiagnosis,
}) => {
  const [task, setTask] = useState('Calculate 2 plus 3 and summarize the company report.');
  const [mode, setMode] = useState<ExecutionMode>('deterministic');
  const [maxToolCalls, setMaxToolCalls] = useState(8);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ExecutionResponse | null>(null);

  if (!isOpen) return null;

  const handleRun = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!task.trim()) return;

    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const resp = await api.submitExecution({
        task: task.trim(),
        mode,
        max_tool_calls: maxToolCalls,
      });
      setResult(resp);
    } catch (err: any) {
      if (err instanceof ApiError) {
        setError(`[${err.code || 'error'}] ${err.detail}`);
      } else {
        setError(err.message || 'Execution submission failed');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="bg-surface border border-surface-border rounded-xl w-full max-w-xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">
        {/* Modal Header */}
        <div className="p-4 border-b border-surface-border flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="p-1.5 rounded-md bg-brand-500/10 text-brand-400">
              <Play size={16} />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-white">Execute Agent</h2>
              <p className="text-xs text-slate-400">Run the AgentGuard agent runtime synchronously</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-md text-slate-400 hover:text-white hover:bg-surface-elevated transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Modal Body */}
        <div className="p-5 overflow-y-auto space-y-4 text-xs">
          <form onSubmit={handleRun} className="space-y-4">
            {/* Task Input */}
            <div>
              <label className="block text-slate-300 font-medium mb-1.5">
                Task Description
              </label>
              <textarea
                value={task}
                onChange={(e) => setTask(e.target.value)}
                placeholder="Enter prompt or query for the agent..."
                rows={3}
                required
                className="w-full px-3 py-2 bg-surface-elevated border border-surface-border rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-brand-500 font-sans text-xs"
              />
            </div>

            {/* Mode & Max Tool Calls Grid */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-slate-300 font-medium mb-1.5">
                  Execution Mode
                </label>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => setMode('deterministic')}
                    className={`flex-1 py-1.5 px-2.5 rounded-lg border text-center transition-all ${
                      mode === 'deterministic'
                        ? 'bg-brand-500/15 border-brand-500/50 text-brand-300 font-medium'
                        : 'bg-surface-elevated border-surface-border text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    Deterministic (Offline)
                  </button>
                  <button
                    type="button"
                    onClick={() => setMode('llm')}
                    className={`flex-1 py-1.5 px-2.5 rounded-lg border text-center transition-all ${
                      mode === 'llm'
                        ? 'bg-brand-500/15 border-brand-500/50 text-brand-300 font-medium'
                        : 'bg-surface-elevated border-surface-border text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    LLM Engine
                  </button>
                </div>
              </div>

              <div>
                <div className="flex justify-between items-center mb-1.5">
                  <label className="text-slate-300 font-medium">Max Tool Calls</label>
                  <span className="font-mono text-brand-400 font-medium">{maxToolCalls}</span>
                </div>
                <input
                  type="range"
                  min="1"
                  max="8"
                  value={maxToolCalls}
                  onChange={(e) => setMaxToolCalls(Number(e.target.value))}
                  className="w-full accent-brand-500 bg-surface-elevated cursor-pointer"
                />
              </div>
            </div>

            {/* Error Message */}
            {error && (
              <div className="p-3 rounded-lg bg-rose-950/40 border border-rose-800/60 text-rose-300 flex items-start gap-2">
                <AlertCircle size={16} className="shrink-0 mt-0.5 text-rose-400" />
                <div className="flex-1 break-words leading-relaxed font-mono text-[11px]">{error}</div>
              </div>
            )}

            {/* Submit Button */}
            <button
              type="submit"
              disabled={loading || !task.trim()}
              className="w-full py-2.5 px-4 rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-50 text-white font-medium flex items-center justify-center gap-2 transition-colors shadow-sm"
            >
              {loading ? (
                <>
                  <Loader2 size={16} className="animate-spin" />
                  <span>Executing Agent...</span>
                </>
              ) : (
                <>
                  <Play size={14} className="fill-current" />
                  <span>Submit Execution</span>
                </>
              )}
            </button>
          </form>

          {/* Execution Result Card */}
          {result && (
            <div className="mt-4 pt-4 border-t border-surface-border space-y-3">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-slate-200 flex items-center gap-1.5">
                  <CheckCircle2 size={15} className="text-emerald-400" />
                  Execution Finished
                </span>
                <span className={`px-2 py-0.5 rounded-full border text-[11px] font-mono ${getStatusBadgeClass(result.status)}`}>
                  {result.status}
                </span>
              </div>

              <div className="bg-surface-elevated p-3 rounded-lg border border-surface-border space-y-2 font-mono text-[11px]">
                <div className="flex justify-between">
                  <span className="text-slate-400">Execution ID:</span>
                  <span className="text-slate-200">{result.execution_id}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Duration:</span>
                  <span className="text-slate-200">{formatDuration(result.duration_ms)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Tool Calls:</span>
                  <span className="text-slate-200">
                    <span className="text-emerald-400">{result.successful_tool_call_count} ok</span>
                    {' / '}
                    <span className="text-rose-400">{result.failed_tool_call_count} failed</span>
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Persisted:</span>
                  <span className={result.persisted ? 'text-emerald-400' : 'text-amber-400'}>
                    {result.persisted ? 'Yes (PostgreSQL)' : 'No (Offline Mode)'}
                  </span>
                </div>
                {result.final_output && (
                  <div className="pt-2 border-t border-surface-border/60">
                    <span className="text-slate-400 block mb-1">Final Output:</span>
                    <div className="text-slate-300 font-sans p-2 rounded bg-background border border-surface-border/50 text-[11px]">
                      {result.final_output}
                    </div>
                  </div>
                )}
              </div>

              {/* Action Buttons to Explore Result */}
              <div className="flex gap-2 pt-1">
                {result.persisted ? (
                  <>
                    <button
                      type="button"
                      onClick={() => {
                        onClose();
                        onNavigateToTrace(result.execution_id);
                      }}
                      className="flex-1 py-2 px-3 rounded-lg bg-surface-elevated hover:bg-surface border border-surface-border text-slate-200 flex items-center justify-center gap-1.5 transition-colors"
                    >
                      <GitBranch size={14} className="text-brand-400" />
                      <span>View Trace</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        onClose();
                        onNavigateToDiagnosis(result.execution_id);
                      }}
                      className="flex-1 py-2 px-3 rounded-lg bg-surface-elevated hover:bg-surface border border-surface-border text-slate-200 flex items-center justify-center gap-1.5 transition-colors"
                    >
                      <ShieldAlert size={14} className="text-amber-400" />
                      <span>Diagnose Trace</span>
                    </button>
                  </>
                ) : (
                  <div className="w-full text-center text-slate-400 text-[11px]">
                    Trace not persisted in database. Run with <code className="text-brand-300">AGENTGUARD_DATABASE_URL</code> to inspect in Trace Explorer.
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
