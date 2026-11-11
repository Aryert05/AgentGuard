import {
  HealthResponse,
  OverviewMetricsResponse,
  ExecutionListResponse,
  ExecutionSummaryResponse,
  ExecutionEventsResponse,
  ExecutionRequest,
  ExecutionResponse,
  DiagnosisResponse,
  ProblemDetails,
} from '../types/api';

const DEFAULT_BASE_URL = '/api/v1';

export class ApiError extends Error {
  status: number;
  code?: string;
  detail: string;
  requestId?: string;

  constructor(status: number, detail: string, code?: string, requestId?: string) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.code = code;
    this.requestId = requestId;
  }
}

class ApiService {
  private baseUrl: string;

  constructor() {
    let url = import.meta.env.VITE_API_BASE_URL || DEFAULT_BASE_URL;
    // Strip trailing slashes
    this.baseUrl = url.replace(/\/+$/, '');
  }

  setBaseUrl(url: string) {
    this.baseUrl = url.replace(/\/+$/, '');
  }

  getBaseUrl(): string {
    return this.baseUrl;
  }

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const url = `${this.baseUrl}${path.startsWith('/') ? path : `/${path}`}`;
    const headers = new Headers(options.headers || {});
    if (!headers.has('Content-Type') && options.body) {
      headers.set('Content-Type', 'application/json');
    }
    headers.set('Accept', 'application/json, application/problem+json');

    let response: Response;
    try {
      response = await fetch(url, { ...options, headers });
    } catch (err: any) {
      throw new ApiError(
        0,
        `Cannot connect to AgentGuard API at ${this.baseUrl}. Please check that the server is running.`,
        'network_error'
      );
    }

    const requestId = response.headers.get('X-Request-ID') || undefined;

    if (!response.ok) {
      let problem: ProblemDetails | null = null;
      try {
        problem = await response.json();
      } catch {
        // Not a JSON error
      }

      const detail = problem?.detail || `HTTP ${response.status}: ${response.statusText}`;
      const code = problem?.code || `http_${response.status}`;
      throw new ApiError(response.status, detail, code, requestId || problem?.request_id);
    }

    return response.json();
  }

  async getHealth(): Promise<HealthResponse> {
    return this.request<HealthResponse>('/health');
  }

  async getOverview(): Promise<OverviewMetricsResponse> {
    return this.request<OverviewMetricsResponse>('/executions/overview');
  }

  async listExecutions(params: {
    limit?: number;
    offset?: number;
    status?: string;
    query?: string;
  } = {}): Promise<ExecutionListResponse> {
    const searchParams = new URLSearchParams();
    if (params.limit !== undefined) searchParams.set('limit', String(params.limit));
    if (params.offset !== undefined) searchParams.set('offset', String(params.offset));
    if (params.status) searchParams.set('status', params.status);
    if (params.query) searchParams.set('query', params.query);

    const queryStr = searchParams.toString();
    const endpoint = `/executions/history${queryStr ? `?${queryStr}` : ''}`;
    return this.request<ExecutionListResponse>(endpoint);
  }

  async getExecution(executionId: string): Promise<ExecutionSummaryResponse> {
    return this.request<ExecutionSummaryResponse>(`/executions/${executionId}`);
  }

  async listEvents(
    executionId: string,
    limit: number = 100,
    afterEventIndex?: number | null
  ): Promise<ExecutionEventsResponse> {
    const searchParams = new URLSearchParams();
    searchParams.set('limit', String(limit));
    if (afterEventIndex !== undefined && afterEventIndex !== null) {
      searchParams.set('after_event_index', String(afterEventIndex));
    }
    return this.request<ExecutionEventsResponse>(
      `/executions/${executionId}/events?${searchParams.toString()}`
    );
  }

  async submitExecution(req: ExecutionRequest): Promise<ExecutionResponse> {
    return this.request<ExecutionResponse>('/executions', {
      method: 'POST',
      body: JSON.stringify(req),
    });
  }

  async diagnoseExecution(executionId: string): Promise<DiagnosisResponse> {
    return this.request<DiagnosisResponse>(`/executions/${executionId}/diagnoses`, {
      method: 'POST',
    });
  }

  async diagnoseEvents(events: any[]): Promise<DiagnosisResponse> {
    return this.request<DiagnosisResponse>('/diagnoses', {
      method: 'POST',
      body: JSON.stringify({ events }),
    });
  }
}

export const api = new ApiService();
