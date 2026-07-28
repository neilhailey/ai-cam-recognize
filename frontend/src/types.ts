export type Verdict = '3-axis' | '4-axis' | '5-axis'

export interface MachinablePct {
  '3axis': number
  '4axis': number
  '5axis': number
}

export interface StlReport {
  verdict: Verdict
  verdict_label: string
  machinable_pct: MachinablePct
  best_rotary_axis: string | null
  enclosed_pct: number
  vertical_wall_pct: number
  n_faces: number
  ray_backend: string
  caveats: string[]
}

export interface OrientationResult {
  current_verdict: Verdict
  current_verdict_label: string
  best_verdict: Verdict
  best_verdict_label: string
  best_up_vector: number[]
  best_up_name: string
  improved: boolean
  description: string
}

export interface SetupStep {
  direction: string
  vector: number[]
  cumulative_coverage_pct: number
}

export interface SetupPlan {
  n_setups: number
  setups: SetupStep[]
  uncoverable_pct: number
  fully_covered: boolean
}

export interface ToolingResult {
  max_tool_diameter: number
  limited: boolean
  reachable_pct: number
}

export interface StlResponse {
  session_id: string
  report: StlReport
  orientation: OrientationResult
  setups: SetupPlan
  tooling: ToolingResult
  glb_url: string
  legend: Record<string, string>
}

export interface PhotoResult {
  verdict: '3-axis' | '4-axis' | '5-axis' | 'uncertain'
  confidence: number
  reasoning: string
  suspected_undercuts: string[]
  available: boolean
  caveat?: string
}

export interface PhotoResponse {
  result: PhotoResult
}
