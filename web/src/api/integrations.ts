import { API_PREFIX, apiFetch } from '@/lib/api'

export interface GoogleStatus {
  connected: boolean
}

export interface GoogleConnectResponse {
  authorization_url: string
}

export function getGoogleStatus() {
  return apiFetch<GoogleStatus>(`${API_PREFIX}/integrations/google/status`)
}

export function getGoogleConnectUrl() {
  return apiFetch<GoogleConnectResponse>(`${API_PREFIX}/integrations/google/connect`)
}

export function disconnectGoogle() {
  return apiFetch<void>(`${API_PREFIX}/integrations/google/disconnect`, {
    method: 'DELETE',
  })
}
