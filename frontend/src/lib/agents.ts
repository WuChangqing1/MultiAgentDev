/**
 * Static UI metadata for the agents.
 *
 * This is presentation only. The authoritative catalogue (models, availability,
 * reasoning policy) comes from `GET /api/agents`; these entries just guarantee
 * that the Agent panel renders every known agent in a stable order even before
 * the backend answers.
 */

export interface AgentMeta {
  key: string
  label: string
  shortLabel: string
  /** Fallback model label, replaced by live data when available. */
  model: string
  isLocal: boolean
  role: string
}

export const AGENT_ORDER = [
  'main',
  'local_extractor',
  'local_summarizer',
  'local_classifier',
  'local_reviewer',
] as const

export const AGENT_META: Record<string, AgentMeta> = {
  main: {
    key: 'main',
    label: 'DeepSeek MainAgent',
    shortLabel: 'MainAgent',
    model: 'DeepSeek',
    isLocal: false,
    role: '任务规划 / 委派决策 / 结果校验 / 最终回答',
  },
  local_extractor: {
    key: 'local_extractor',
    label: 'LocalExtractorAgent',
    shortLabel: 'Extractor',
    model: 'MiniCPM5-2B',
    isLocal: true,
    role: '信息抽取 / 实体与字段提取 / 文本转 JSON',
  },
  local_summarizer: {
    key: 'local_summarizer',
    label: 'LocalSummarizerAgent',
    shortLabel: 'Summarizer',
    model: 'MiniCPM5-2B',
    isLocal: true,
    role: '摘要 / 长文本压缩 / 上下文精简',
  },
  local_classifier: {
    key: 'local_classifier',
    label: 'LocalClassifierAgent',
    shortLabel: 'Classifier',
    model: 'MiniCPM5-2B',
    isLocal: true,
    role: '文本分类 / 意图识别 / 标签判定',
  },
  local_reviewer: {
    key: 'local_reviewer',
    label: 'LocalReviewerAgent',
    shortLabel: 'Reviewer',
    model: 'MiniCPM5-2B',
    isLocal: true,
    role: '格式校验 / 完整性检查 / 一致性核查',
  },
}

export function agentMeta(key?: string | null): AgentMeta {
  if (key && AGENT_META[key]) return AGENT_META[key]
  return {
    key: key ?? 'unknown',
    label: key ?? 'Unknown agent',
    shortLabel: key ?? 'unknown',
    model: '—',
    isLocal: Boolean(key?.startsWith('local_')),
    role: '',
  }
}

/** Stage strings emitted by the orchestrator, rendered as human phrases. */
export const STAGE_LABEL: Record<string, string> = {
  start: 'Starting',
  planning: 'Planning task…',
  reasoning: 'Reasoning about results…',
  extracting: 'Extracting structured information…',
  summarizing: 'Summarising content…',
  classifying: 'Classifying input…',
  reviewing: 'Reviewing worker output…',
  finalizing: 'Composing the final answer…',
  working: 'Working…',
  done: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

export function stageLabel(stage?: string | null): string {
  if (!stage) return ''
  return STAGE_LABEL[stage] ?? stage
}

export const DEMO_PROMPTS = [
  {
    title: '提取 + 摘要',
    hint: '委派给 Local Worker',
    text: '请分析下面这段内容，提取关键信息并总结。\n张三今年20岁，是软件工程专业学生，目前正在学习大模型和Agent开发。',
  },
  {
    title: '复杂推理',
    hint: 'MainAgent 独立完成',
    text: '证明：对任意正整数 n，n^3 - n 能被 6 整除。',
  },
  {
    title: '结构化抽取',
    hint: 'Extractor + Reviewer',
    text: '从下面的会议记录中抽取参会人、时间、议题和待办事项，并以 JSON 输出：\n周二下午两点，产品与研发在 3 楼会议室同步了 v2 版本进度。张伟负责整理接口文档，下周一前完成；李娜跟进灰度发布的监控指标。',
  },
]
