import React from 'react';
import {
  LayoutDashboard,
  Play,
  Activity,
  GitBranch,
  ShieldAlert,
  Lightbulb,
  ExternalLink,
  Shield,
  CheckCircle2,
  AlertTriangle,
  XCircle,
} from 'lucide-react';

export type NavigationTab = 'overview' | 'executions' | 'trace' | 'intelligence' | 'recommendations';

interface SidebarProps {
  currentTab: NavigationTab;
  onSelectTab: (tab: NavigationTab) => void;
  onOpenRunModal: () => void;
  backendStatus: 'connected' | 'disconnected' | 'checking';
  persistenceStatus: 'configured' | 'unconfigured';
}

export const Sidebar: React.FC<SidebarProps> = ({
  currentTab,
  onSelectTab,
  onOpenRunModal,
  backendStatus,
  persistenceStatus,
}) => {
  const navItems: { id: NavigationTab; label: string; icon: React.ReactNode; badge?: string }[] = [
    { id: 'overview', label: 'Overview', icon: <LayoutDashboard size={18} /> },
    { id: 'executions', label: 'Executions', icon: <Activity size={18} /> },
    { id: 'trace', label: 'Trace Explorer', icon: <GitBranch size={18} /> },
    { id: 'intelligence', label: 'Failure Intelligence', icon: <ShieldAlert size={18} /> },
    { id: 'recommendations', label: 'Recommendations', icon: <Lightbulb size={18} /> },
  ];

  return (
    <aside className="w-64 bg-surface border-r border-surface-border flex flex-col h-screen select-none shrink-0">
      {/* Brand Header */}
      <div className="p-5 border-b border-surface-border flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="h-9 w-9 rounded-lg bg-brand-500/10 border border-brand-500/30 flex items-center justify-center text-brand-500 shadow-sm">
            <Shield size={20} className="stroke-[2.2]" />
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <span className="font-bold text-base text-white tracking-tight">AgentGuard</span>
              <span className="text-[10px] px-1.5 py-0.2 bg-brand-500/10 text-brand-400 border border-brand-500/20 rounded font-mono font-medium">v0.1</span>
            </div>
            <p className="text-[11px] text-slate-400 font-medium leading-none mt-0.5">Reliability Platform</p>
          </div>
        </div>
      </div>

      {/* Quick Run Action */}
      <div className="p-3">
        <button
          onClick={onOpenRunModal}
          className="w-full flex items-center justify-center gap-2 px-3.5 py-2.5 rounded-lg bg-brand-600 hover:bg-brand-500 text-white font-medium text-xs transition-colors shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500/40"
        >
          <Play size={14} className="fill-current" />
          <span>Execute Agent</span>
        </button>
      </div>

      {/* Main Navigation */}
      <nav className="flex-1 px-3 py-2 space-y-1 overflow-y-auto">
        <div className="px-3 py-1.5 text-[10px] uppercase tracking-wider font-semibold text-slate-500">
          Platform
        </div>
        {navItems.map((item) => {
          const isActive = currentTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onSelectTab(item.id)}
              className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-xs font-medium transition-all ${
                isActive
                  ? 'bg-brand-500/10 text-brand-400 border border-brand-500/30 shadow-sm'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-surface-elevated border border-transparent'
              }`}
            >
              <div className="flex items-center gap-2.5">
                <span className={isActive ? 'text-brand-400' : 'text-slate-400'}>{item.icon}</span>
                <span>{item.label}</span>
              </div>
              {item.badge && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-elevated text-slate-400 font-mono">
                  {item.badge}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {/* System Status & Footer */}
      <div className="p-3 border-t border-surface-border bg-surface-elevated/40 text-xs">
        <div className="space-y-2">
          {/* Backend Connection */}
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-slate-400 flex items-center gap-1.5">
              Backend API
            </span>
            <div className="flex items-center gap-1 font-mono">
              {backendStatus === 'connected' ? (
                <>
                  <CheckCircle2 size={12} className="text-emerald-400" />
                  <span className="text-emerald-400">Online</span>
                </>
              ) : backendStatus === 'checking' ? (
                <span className="text-slate-400">Checking...</span>
              ) : (
                <>
                  <XCircle size={12} className="text-rose-400" />
                  <span className="text-rose-400">Offline</span>
                </>
              )}
            </div>
          </div>

          {/* Database Persistence */}
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-slate-400 flex items-center gap-1.5">
              Persistence
            </span>
            <div className="flex items-center gap-1 font-mono">
              {persistenceStatus === 'configured' ? (
                <span className="text-emerald-400">PostgreSQL</span>
              ) : (
                <>
                  <AlertTriangle size={12} className="text-amber-400" />
                  <span className="text-amber-400">Disabled</span>
                </>
              )}
            </div>
          </div>
        </div>

        <div className="mt-3 pt-3 border-t border-surface-border/50 flex items-center justify-between text-[11px] text-slate-500">
          <span>FastAPI v1</span>
          <a
            href="/docs"
            target="_blank"
            rel="noreferrer"
            className="hover:text-slate-300 flex items-center gap-1 transition-colors"
          >
            <span>Swagger</span>
            <ExternalLink size={10} />
          </a>
        </div>
      </div>
    </aside>
  );
};
