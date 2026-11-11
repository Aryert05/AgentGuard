import React, { useState, useEffect } from 'react';
import { Sidebar, NavigationTab } from './components/Sidebar';
import { RunModal } from './components/RunModal';
import { OverviewPage } from './pages/OverviewPage';
import { ExecutionsPage } from './pages/ExecutionsPage';
import { TraceExplorerPage } from './pages/TraceExplorerPage';
import { FailureIntelligencePage } from './pages/FailureIntelligencePage';
import { RecommendationsPage } from './pages/RecommendationsPage';
import { DiagnosisResponse } from './types/api';
import { api } from './services/api';
import { Play } from 'lucide-react';

export const App: React.FC = () => {
  const [currentTab, setCurrentTab] = useState<NavigationTab>('overview');
  const [activeExecutionId, setActiveExecutionId] = useState<string | null>(null);
  const [activeDiagnosis, setActiveDiagnosis] = useState<DiagnosisResponse | null>(null);
  const [isRunModalOpen, setIsRunModalOpen] = useState(false);

  const [backendStatus, setBackendStatus] = useState<'connected' | 'disconnected' | 'checking'>('checking');
  const [persistenceStatus, setPersistenceStatus] = useState<'configured' | 'unconfigured'>('unconfigured');

  // Check backend health & persistence availability
  const checkHealth = async () => {
    try {
      const health = await api.getHealth();
      if (health.status === 'ok') {
        setBackendStatus('connected');
      } else {
        setBackendStatus('disconnected');
      }

      // Check if overview endpoint responds to see if database persistence is configured
      try {
        await api.getOverview();
        setPersistenceStatus('configured');
      } catch (err: any) {
        if (err.code === 'persistence_not_configured' || err.status === 503) {
          setPersistenceStatus('unconfigured');
        }
      }
    } catch {
      setBackendStatus('disconnected');
    }
  };

  useEffect(() => {
    checkHealth();
    const interval = setInterval(checkHealth, 30000);
    return () => clearInterval(interval);
  }, []);

  const handleNavigateToTrace = (executionId?: string) => {
    if (executionId) {
      setActiveExecutionId(executionId);
    }
    setCurrentTab('trace');
  };

  const handleNavigateToDiagnosis = (executionId?: string) => {
    if (executionId) {
      setActiveExecutionId(executionId);
    }
    setCurrentTab('intelligence');
  };

  const handleNavigateToRecommendations = (diagnosis: DiagnosisResponse) => {
    setActiveDiagnosis(diagnosis);
    setCurrentTab('recommendations');
  };

  return (
    <div className="flex h-screen bg-background text-slate-100 overflow-hidden font-sans">
      {/* Persistent Navigation Sidebar */}
      <Sidebar
        currentTab={currentTab}
        onSelectTab={setCurrentTab}
        onOpenRunModal={() => setIsRunModalOpen(true)}
        backendStatus={backendStatus}
        persistenceStatus={persistenceStatus}
      />

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Top Header Bar */}
        <header className="h-14 border-b border-surface-border bg-surface/50 backdrop-blur-md px-6 flex items-center justify-between shrink-0">
          <div className="flex items-center gap-2 text-xs font-medium text-slate-400">
            <span>AgentGuard</span>
            <span className="text-slate-600">/</span>
            <span className="text-white capitalize font-semibold">{currentTab.replace('_', ' ')}</span>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => setIsRunModalOpen(true)}
              className="px-3 py-1.5 rounded-lg bg-brand-600 hover:bg-brand-500 text-white text-xs font-medium transition-colors flex items-center gap-1.5 shadow-sm"
            >
              <Play size={12} className="fill-current" />
              <span>Execute Agent</span>
            </button>
          </div>
        </header>

        {/* Scrollable View Area */}
        <main className="flex-1 overflow-y-auto">
          {currentTab === 'overview' && (
            <OverviewPage
              onNavigateToExecutions={() => setCurrentTab('executions')}
              onNavigateToTrace={handleNavigateToTrace}
              onNavigateToDiagnosis={handleNavigateToDiagnosis}
              onOpenRunModal={() => setIsRunModalOpen(true)}
              persistenceStatus={persistenceStatus}
            />
          )}

          {currentTab === 'executions' && (
            <ExecutionsPage
              onSelectExecutionForTrace={handleNavigateToTrace}
              onSelectExecutionForDiagnosis={handleNavigateToDiagnosis}
              onOpenRunModal={() => setIsRunModalOpen(true)}
            />
          )}

          {currentTab === 'trace' && (
            <TraceExplorerPage
              initialExecutionId={activeExecutionId}
              onNavigateToDiagnosis={handleNavigateToDiagnosis}
            />
          )}

          {currentTab === 'intelligence' && (
            <FailureIntelligencePage
              initialExecutionId={activeExecutionId}
              onNavigateToRecommendations={handleNavigateToRecommendations}
              onNavigateToTrace={handleNavigateToTrace}
            />
          )}

          {currentTab === 'recommendations' && (
            <RecommendationsPage
              diagnosisData={activeDiagnosis}
              onNavigateToIntelligence={() => setCurrentTab('intelligence')}
            />
          )}
        </main>
      </div>

      {/* Execute Agent Dialog Modal */}
      <RunModal
        isOpen={isRunModalOpen}
        onClose={() => setIsRunModalOpen(false)}
        onNavigateToTrace={handleNavigateToTrace}
        onNavigateToDiagnosis={handleNavigateToDiagnosis}
      />
    </div>
  );
};

export default App;
