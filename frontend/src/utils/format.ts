export function formatDuration(ms?: number | null): string {
  if (ms === undefined || ms === null) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

export function formatDateTime(isoString?: string | null): string {
  if (!isoString) return '—';
  try {
    const d = new Date(isoString);
    return d.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
  } catch {
    return isoString;
  }
}

export function formatShortId(id?: string | null): string {
  if (!id) return '—';
  if (id.length <= 12) return id;
  return `${id.slice(0, 8)}...${id.slice(-4)}`;
}

export function getStatusBadgeClass(status: string): string {
  switch (status.toLowerCase()) {
    case 'completed':
    case 'success':
      return 'bg-emerald-950/70 text-emerald-400 border-emerald-800/60';
    case 'failed':
      return 'bg-rose-950/70 text-rose-400 border-rose-800/60';
    case 'partially_completed':
      return 'bg-amber-950/70 text-amber-400 border-amber-800/60';
    case 'running':
      return 'bg-blue-950/70 text-blue-400 border-blue-800/60 animate-pulse';
    case 'pending':
      return 'bg-slate-900 text-slate-400 border-slate-700';
    default:
      return 'bg-slate-900 text-slate-300 border-slate-700';
  }
}

export function getRiskBadgeClass(risk: string): string {
  switch (risk.toLowerCase()) {
    case 'low':
      return 'bg-emerald-950/60 text-emerald-400 border-emerald-800/50';
    case 'medium':
      return 'bg-amber-950/60 text-amber-400 border-amber-800/50';
    case 'high':
    case 'critical':
      return 'bg-rose-950/60 text-rose-400 border-rose-800/50';
    default:
      return 'bg-slate-900 text-slate-300 border-slate-700';
  }
}
