import React, { useEffect, useState } from 'react';
import {
  ShieldAlert,
  Search,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  BrainCircuit,
  Cpu,
  Layers,
  ArrowRight,
  RotateCw,
  Lightbulb,
  FileCode,
  ShieldCheck,
  Check,
  Copy,
} from 'lucide-react';
import { api, ApiError } from '../services/api';
import { DiagnosisResponse } from '../types/api';
import { getRiskBadgeClass } from '../utils/format';

interface FailureIntelligencePageProps {
  initialExecutionId?: string | null;
  onNavigateToRecommendations: (diagnosis: DiagnosisResponse) => void;
  onNavigateToTrace: (executionId: string) => void;
}

export const FailureIntelligencePage: React.FC<FailureIntelligencePageProps> = ({
  initialExecutionId,
  onNavigateToRecommendations,
  onNavigateToTrace,
}) => {
  const [activeTab, setActiveTab] = useState<'persisted' | 'custom'>('persisted');
  const [executionIdInput, setExecutionIdInput] = useState(initialExecutionId || '');
  const [customEventsJson, setCustomEventsJson] = useState('');

  const [diagnosisResult, setDiagnosisResult] = useState<DiagnosisResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (initialExecutionId) {
      setExecutionIdInput(initialExecutionId);
      runPersistedDiagnosis(initialExecutionId);
    }
  }, [initialExecutionId]);

  const runPersistedDiagnosis = async (id: string) => {
    if (!id.trim()) return;
    setLoading(true);
    setError(null);
    setDiagnosisResult(null);

    try {
      const resp = await api.diagnoseExecution(id.trim());
      setDiagnosisResult(resp);
    } catch (err: any) {
      if (err instanceof ApiError && err.code === 'execution_not_found') {
        setError(`Execution ${id} was not found in persistence store.`);
      } else if (err instanceof ApiError && err.code === 'diagnosis_trace_too_large') {
        setError('Persisted execution exceeds the 1,000-event diagnosis limit.');
      } else if (err instanceof ApiError && (err.code === 'persistence_not_configured' || err.code === 'persistence_unavailable')) {
        setError('Persistence store is currently unavailable or unconfigured.');
      } else if (err instanceof ApiError) {
        setError(`[${err.code || err.status}] ${err.detail}`);
      } else {
        setError(err.message || 'Diagnosis evaluation failed');
      }
    } finally {
      setLoading(false);
    }
  };

  const runCustomEventsDiagnosis = async () => {
    if (!customEventsJson.trim()) return;
    setLoading(true);
    setError(null);
    setDiagnosisResult(null);

    try {
      let parsed: any;
      try {
        parsed = JSON.parse(customEventsJson);
      } catch {
        throw new Error('Invalid JSON format. Expected an array of ExecutionEvent objects.');
      }

      const events = Array.isArray(parsed) ? parsed : parsed.events;
      if (!Array.isArray(events)) {
        throw new Error('Events must be an array of event objects or a JSON object with an "events" key.');
      }

      const resp = await api.diagnoseEvents(events);
      setDiagnosisResult(resp);
    } catch (err: any) {
      if (err instanceof ApiError) {
        setError(`[${err.code || err.status}] ${err.detail}`);
      } else {
        setError(err.message || 'Stateless diagnosis failed');
      }
    } finally {
      setLoading(false);
    }
  };

  const sampleEventsTemplate = () => {
    const sample = [
      {
        event_id: 'evt-1',
        execution_id: 'sample-exec',
        task_id: 'sample-task',
        step_number: 0,
        timestamp: new Date().toISOString(),
        event_type: 'execution_started',
        component: 'agent',
        status: 'running',
        metadata: {},
      },
      {
        event_id: 'evt-2',
        execution_id: 'sample-exec',
        task_id: 'sample-task',
        step_number: 1,
        timestamp: new Date().toISOString(),
        event_type: 'tool_failed',
        component: 'tool',
        tool_name: 'calculator',
        status: 'failed',
        error_type: 'TimeoutError',
        error_message: 'Operation timed out after 3000ms',
        metadata: {},
      },
    ];
    setCustomEventsJson(JSON.stringify(sample, null, 2));
  };

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
            Failure Intelligence
          </h1>
          <p className="text-xs text-slate-400 mt-1">
            Rule-based root-cause identification combined with safe predictive ML risk signals
          </p>
        </div>

        {diagnosisResult && (
          <button
            onClick={() => onNavigateToRecommendations(diagnosisResult)}
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-brand-600 hover:bg-brand-500 text-white text-xs font-medium transition-colors shadow-sm"
          >
            <Lightbulb size={14} />
            <span>View Recommendations ({diagnosisResult.recommendations.length})</span>
          </button>
        )}
      </div>

      {/* Mode Selector Tabs */}
      <div className="flex border-b border-surface-border gap-6 text-xs font-medium">
        <button
          onClick={() => setActiveTab('persisted')}
          className={`pb-2.5 flex items-center gap-2 border-b-2 transition-colors ${
            activeTab === 'persisted'
              ? 'border-brand-500 text-brand-400'
              : 'border-transparent text-slate-400 hover:text-slate-200'
          }`}
        >
          <Cpu size={15} />
          <span>Diagnose Persisted Execution</span>
        </button>
        <button
          onClick={() => setActiveTab('custom')}
          className={`pb-2.5 flex items-center gap-2 border-b-2 transition-colors ${
            activeTab === 'custom'
              ? 'border-brand-500 text-brand-400'
              : 'border-transparent text-slate-400 hover:text-slate-200'
          }`}
        >
          <FileCode size={15} />
          <span>Stateless Event Sequence (JSON)</span>
        </button>
      </div>

      {/* Tab 1: Persisted Execution Input */}
      {activeTab === 'persisted' && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            runPersistedDiagnosis(executionIdInput);
          }}
          className="flex gap-2"
        >
          <div className="relative flex-1">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              type="text"
              placeholder="Enter Execution ID to analyze (e.g. 7f3ef2a5-...)"
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
            {loading ? <RotateCw size={14} className="animate-spin" /> : <BrainCircuit size={14} />}
            <span>Analyze Execution</span>
          </button>
        </form>
      )}

      {/* Tab 2: Custom Event Sequence Input */}
      {activeTab === 'custom' && (
        <div className="space-y-3 bg-surface p-4 rounded-xl border border-surface-border text-xs">
          <div className="flex justify-between items-center">
            <label className="font-semibold text-slate-200">
              ExecutionEvent Array (JSON)
            </label>
            <button
              type="button"
              onClick={sampleEventsTemplate}
              className="text-brand-400 hover:text-brand-300 font-mono text-[11px] underline"
            >
              Load Sample Error Prefix
            </button>
          </div>
          <textarea
            value={customEventsJson}
            onChange={(e) => setCustomEventsJson(e.target.value)}
            rows={6}
            placeholder='[ { "event_id": "evt-1", "event_type": "execution_started", ... } ]'
            className="w-full p-3 bg-surface-elevated border border-surface-border rounded-lg text-slate-200 font-mono text-[11px] focus:outline-none focus:ring-1 focus:ring-brand-500"
          />
          <button
            onClick={runCustomEventsDiagnosis}
            disabled={loading || !customEventsJson.trim()}
            className="px-4 py-2 bg-brand-600 hover:bg-brand-500 disabled:opacity-50 text-white rounded-lg text-xs font-medium transition-colors flex items-center gap-1.5"
          >
            {loading ? <RotateCw size={14} className="animate-spin" /> : <BrainCircuit size={14} />}
            <span>Diagnose Event Prefix</span>
          </button>
        </div>
      )}

      {/* Error Banner */}
      {error && (
        <div className="p-4 rounded-xl bg-rose-950/40 border border-rose-800/60 flex items-start gap-3 text-xs text-rose-300">
          <AlertTriangle size={18} className="text-rose-400 shrink-0 mt-0.5" />
          <div>
            <div className="font-semibold text-rose-200">Diagnosis Execution Error</div>
            <div className="font-mono text-[11px] mt-0.5">{error}</div>
          </div>
        </div>
      )}

      {/* Diagnosis Results Display */}
      {diagnosisResult && (
        <div className="space-y-5">
          {/* Main Diagnosis Summary Card */}
          <div className="p-5 rounded-xl bg-surface border border-surface-border space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-surface-border">
              <div className="flex items-center gap-2.5">
                {diagnosisResult.diagnosis.is_failure ? (
                  <div className="p-2 rounded-lg bg-rose-500/10 text-rose-400 border border-rose-500/20">
                    <ShieldAlert size={20} />
                  </div>
                ) : (
                  <div className="p-2 rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                    <ShieldCheck size={20} />
                  </div>
                )}
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-sm font-bold text-white tracking-tight">
                      {diagnosisResult.diagnosis.is_failure ? 'Failure Identified' : 'Clean Execution'}
                    </h2>
                    <span className="px-2 py-0.5 rounded text-[10px] font-mono font-medium uppercase bg-surface-elevated text-slate-300 border border-surface-border">
                      {diagnosisResult.diagnosis.primary_category}
                    </span>
                  </div>
                  <p className="text-xs text-slate-400 mt-0.5">
                    Deterministic root-cause diagnosis from telemetry traces
                  </p>
                </div>
              </div>

              {/* Confidence Meter */}
              <div className="flex items-center gap-3 bg-surface-elevated px-3 py-1.5 rounded-lg border border-surface-border font-mono text-xs">
                <span className="text-slate-400">Confidence:</span>
                <span className="text-brand-400 font-bold">
                  {(diagnosisResult.diagnosis.confidence * 100).toFixed(0)}%
                </span>
              </div>
            </div>

            {/* Explanation Summary */}
            <div className="text-xs text-slate-200 bg-surface-elevated/70 p-3 rounded-lg border border-surface-border/50 leading-relaxed font-sans">
              <span className="text-slate-500 font-mono text-[10px] uppercase block mb-1">Diagnosis Summary</span>
              {diagnosisResult.diagnosis.summary}
            </div>

            {/* Meta details grid */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs font-mono pt-1 text-slate-400">
              <div>
                <span className="text-slate-500 block text-[10px]">EXECUTION ID</span>
                <span className="text-slate-200">{diagnosisResult.diagnosis.execution_id || 'Stateless'}</span>
              </div>
              <div>
                <span className="text-slate-500 block text-[10px]">TARGET TOOL</span>
                <span className="text-slate-200">{diagnosisResult.diagnosis.tool_name || 'None'}</span>
              </div>
              <div>
                <span className="text-slate-500 block text-[10px]">TERMINAL REACHED</span>
                <span className={diagnosisResult.diagnosis.terminal_observed ? 'text-rose-400' : 'text-slate-400'}>
                  {diagnosisResult.diagnosis.terminal_observed ? 'Yes' : 'No'}
                </span>
              </div>
              <div>
                <span className="text-slate-500 block text-[10px]">PARTIAL RESULTS</span>
                <span className="text-slate-300">
                  {diagnosisResult.diagnosis.partial_results_available ? 'Available' : 'None'}
                </span>
              </div>
            </div>
          </div>

          {/* ML Risk Assessment Card */}
          <div className="p-5 rounded-xl bg-surface border border-surface-border space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <BrainCircuit size={17} className="text-brand-400" />
                <h3 className="text-xs font-semibold text-white uppercase tracking-wider">
                  Predictive ML Risk Signal
                </h3>
              </div>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-surface-elevated text-slate-400 border border-surface-border">
                Predictive Only (Non-Authoritative)
              </span>
            </div>

            {diagnosisResult.diagnosis.ml_risk ? (
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 pt-2">
                <div className="p-3 bg-surface-elevated rounded-lg border border-surface-border">
                  <span className="text-slate-400 text-xs block mb-1">Failure Probability</span>
                  <div className="text-xl font-bold font-mono text-white">
                    {(diagnosisResult.diagnosis.ml_risk.probability * 100).toFixed(1)}%
                  </div>
                  <div className="text-[11px] text-slate-500 font-mono mt-0.5">
                    Threshold: {(diagnosisResult.diagnosis.ml_risk.threshold * 100).toFixed(1)}%
                  </div>
                </div>

                <div className="p-3 bg-surface-elevated rounded-lg border border-surface-border">
                  <span className="text-slate-400 text-xs block mb-1">Prediction Outcome</span>
                  <div className={`text-base font-bold font-mono ${
                    diagnosisResult.diagnosis.ml_risk.predicted_failure ? 'text-rose-400' : 'text-emerald-400'
                  }`}>
                    {diagnosisResult.diagnosis.ml_risk.predicted_failure ? 'Predicted Failure' : 'Predicted Safe'}
                  </div>
                  <div className="text-[11px] text-slate-500 font-mono mt-0.5">
                    Source: {diagnosisResult.diagnosis.ml_risk.source}
                  </div>
                </div>

                <div className="p-3 bg-surface-elevated rounded-lg border border-surface-border">
                  <span className="text-slate-400 text-xs block mb-1">Model Name</span>
                  <div className="text-sm font-mono text-slate-200">
                    {diagnosisResult.diagnosis.ml_risk.model_name || 'Standard Risk Engine'}
                  </div>
                  <div className="text-[11px] text-slate-500 mt-0.5 font-sans">
                    Statistical inference layer
                  </div>
                </div>
              </div>
            ) : (
              <div className="p-3 bg-surface-elevated/50 rounded-lg text-slate-400 text-xs font-sans">
                {diagnosisResult.ml_error ? (
                  <span className="text-amber-300">
                    Notice: {diagnosisResult.ml_error} Deterministic rule diagnosis was successfully computed.
                  </span>
                ) : (
                  <span>
                    No ML risk model is currently configured on this server. AgentGuard evaluated deterministic telemetry patterns.
                  </span>
                )}
              </div>
            )}
          </div>

          {/* Detected Failure Patterns */}
          {diagnosisResult.diagnosis.patterns.length > 0 && (
            <div className="p-5 rounded-xl bg-surface border border-surface-border space-y-3">
              <h3 className="text-xs font-semibold text-white uppercase tracking-wider flex items-center gap-2">
                <Layers size={16} className="text-brand-400" />
                <span>Detected Failure Patterns ({diagnosisResult.diagnosis.patterns.length})</span>
              </h3>

              <div className="space-y-2">
                {diagnosisResult.diagnosis.patterns.map((pat, idx) => (
                  <div
                    key={idx}
                    className="p-3 rounded-lg bg-surface-elevated border border-surface-border flex items-start justify-between gap-4 text-xs"
                  >
                    <div className="space-y-1 font-sans">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-white">{pat.pattern_type}</span>
                        {pat.tool_name && (
                          <span className="px-1.5 py-0.5 rounded bg-brand-500/10 text-brand-300 font-mono text-[10px]">
                            {pat.tool_name}
                          </span>
                        )}
                      </div>
                      <p className="text-slate-300 leading-relaxed text-[11px]">{pat.description}</p>
                    </div>

                    <div className="shrink-0 text-right font-mono text-[11px]">
                      <span className={`px-2 py-0.5 rounded border capitalize ${getRiskBadgeClass(pat.severity)}`}>
                        {pat.severity}
                      </span>
                      <div className="text-slate-500 text-[10px] mt-1">Count: {pat.count}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Evidence List */}
          {diagnosisResult.diagnosis.evidence.length > 0 && (
            <div className="p-5 rounded-xl bg-surface border border-surface-border space-y-3">
              <h3 className="text-xs font-semibold text-white uppercase tracking-wider">
                Telemetry Evidence
              </h3>

              <div className="space-y-2 font-mono text-xs">
                {diagnosisResult.diagnosis.evidence.map((ev, idx) => (
                  <div
                    key={idx}
                    className="p-3 rounded-lg bg-surface-elevated border border-surface-border flex items-center justify-between text-[11px]"
                  >
                    <div className="space-y-0.5 font-sans">
                      <div className="text-slate-200">{ev.description}</div>
                      <div className="text-[10px] text-slate-500 font-mono">
                        Source: {ev.source} {ev.event_ids.length > 0 ? `• Events: [${ev.event_ids.join(', ')}]` : ''}
                      </div>
                    </div>

                    {ev.observed_value !== null && ev.observed_value !== undefined && (
                      <div className="text-right text-slate-300 shrink-0 font-mono">
                        <span>Observed: {ev.observed_value}</span>
                        {ev.threshold !== null && ev.threshold !== undefined && (
                          <span className="text-slate-500"> / {ev.threshold}</span>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
