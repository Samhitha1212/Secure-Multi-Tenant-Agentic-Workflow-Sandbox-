
import { useEffect, useState } from 'react'
import {
  Braces,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Code2,
  Cpu,
  Database,
  FileCode2,
  KeyRound,
  LoaderCircle,
  LogOut,
  Pause,
  Play,
  RotateCcw,
  Terminal,
  Workflow,
} from 'lucide-react'
import './App.css'

type QuestionType = 'python' | 'workflow'

interface ResourceLimits {
  cpu: string
  memory: string
  timeout_seconds: number
  max_output_bytes: number
}

interface QuestionSummary {
  id: string
  title: string
  type: QuestionType
  description: string
  allowed_tools: string[]
  available_tools: string[]
  required_tools: string[]
  permissions_satisfied: boolean
  resource_limits: ResourceLimits
}

interface QuestionDetails extends QuestionSummary {
  statement: string
  requirements: string[]
  starter_code: string | null
  starter_workflow: Record<string, unknown> | null
}

interface ExecutionResponse {
  question_id: string
  execution_id: string
  submission_type: QuestionType
  status: string
  tools_used?: string[]
  step_results?: Record<string, Record<string, unknown>>
  execution_result?: {
    sandbox_id: string
    stdout: string
    stderr: string
    exit_code: number | null
    timed_out: boolean
    duration_seconds: number
    output_truncated: boolean
    oom_killed: boolean
    output?: Record<string, unknown> | null
  }
}

interface QuestionsResponse {
  questions: QuestionSummary[]
}

interface DemoUser {
  user_id: string
  email: string
  tenant_id: string
  role: string
}

interface DemoUsersResponse {
  users: DemoUser[]
}

interface LoginResponse {
  access_token: string
  token_type: string
  user: DemoUser
}

interface SubmissionPayload {
  source?: string
  specification?: Record<string, unknown>
  execution_id?: string
}

const PAUSE_RETRY_COUNT = 12
const PAUSE_RETRY_DELAY_MS = 250

async function responseError(response: Response): Promise<string> {
  const body = await response.json().catch(() => null)

  if (body && typeof body.detail === 'string') {
    return body.detail
  }

  return `Request failed with HTTP ${response.status}`
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
  }

  return `${Math.round(bytes / 1024)} KiB`
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

function App() {
  const [demoUsers, setDemoUsers] = useState<DemoUser[]>([])
  const [selectedDemoEmail, setSelectedDemoEmail] = useState('')
  const [demoUsersLoading, setDemoUsersLoading] = useState(true)
  const [connectedUser, setConnectedUser] = useState<DemoUser | null>(null)
  const [connectedKey, setConnectedKey] = useState<string | null>(null)
  const [questions, setQuestions] = useState<QuestionSummary[]>([])
  const [selectedQuestion, setSelectedQuestion] =
    useState<QuestionDetails | null>(null)
  const [editorValue, setEditorValue] = useState('')
  const [executionResult, setExecutionResult] =
    useState<ExecutionResponse | null>(null)

  const [connecting, setConnecting] = useState(false)
  const [loadingQuestion, setLoadingQuestion] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [pausing, setPausing] = useState(false)
  const [resuming, setResuming] = useState(false)

  const [activeExecutionId, setActiveExecutionId] = useState<string | null>(null)
  const [pauseRequested, setPauseRequested] = useState(false)
  const [executionPaused, setExecutionPaused] = useState(false)

  const [notice, setNotice] = useState('')
  const [noticeType, setNoticeType] = useState<'error' | 'success'>('error')

  const isConnected = connectedKey !== null

  // Keep the selected question and editor stable while an execution is active.
  const executionLocked =
    submitting ||
    pausing ||
    resuming ||
    executionPaused ||
    activeExecutionId !== null

  useEffect(() => {
      let cancelled = false

      async function loadDemoUsers() {
        try {
          const response = await fetch('/api/auth/demo-users')

          if (!response.ok) {
            throw new Error(await responseError(response))
          }

          const data = (await response.json()) as DemoUsersResponse

          if (!cancelled) {
            setDemoUsers(data.users)
            setSelectedDemoEmail(data.users[0]?.email ?? '')
          }
        } catch (error) {
          if (!cancelled) {
            setNoticeType('error')
            setNotice(
              error instanceof Error
                ? error.message
                : 'Unable to load demo users.',
            )
          }
        } finally {
          if (!cancelled) {
            setDemoUsersLoading(false)
          }
        }
      }

      void loadDemoUsers()

      return () => {
        cancelled = true
      }
    }, [])


    async function selectQuestion(question: QuestionSummary) {
    if (!connectedKey || executionLocked) return

    setLoadingQuestion(true)
    setNotice('')

    try {
      const response = await fetch(`/api/questions/${question.id}`, {
        headers: {
          Authorization: `Bearer ${connectedKey}`,
        },
      })

      if (!response.ok) {
        throw new Error(await responseError(response))
      }

      const details = (await response.json()) as QuestionDetails

      setSelectedQuestion(details)
      setEditorValue(
        details.type === 'python'
          ? details.starter_code ?? ''
          : JSON.stringify(details.starter_workflow ?? {}, null, 2),
      )
      setExecutionResult(null)
      setActiveExecutionId(null)
      setPauseRequested(false)
      setExecutionPaused(false)
    } catch (error) {
      setNoticeType('error')
      setNotice(
        error instanceof Error
          ? error.message
          : 'Unable to load the selected question.',
      )
    } finally {
      setLoadingQuestion(false)
    }
  }

  async function connectToPlatform() {
  if (!selectedDemoEmail) {
    setNoticeType('error')
    setNotice('Select a demo user.')
    return
  }

  setConnecting(true)
  setNotice('')

  try {
    const loginResponse = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: selectedDemoEmail }),
    })

    if (!loginResponse.ok) {
      throw new Error(await responseError(loginResponse))
    }

    const loginData = (await loginResponse.json()) as LoginResponse
    const token = loginData.access_token

    const questionsResponse = await fetch('/api/questions', {
      headers: { Authorization: `Bearer ${token}` },
    })

    if (!questionsResponse.ok) {
      // Do not leave an unused session active if loading questions fails.
      await fetch('/api/auth/logout', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      }).catch(() => undefined)

      throw new Error(await responseError(questionsResponse))
    }

    const questionsData =
      (await questionsResponse.json()) as QuestionsResponse

    setQuestions(questionsData.questions)
    setConnectedKey(token)
    setConnectedUser(loginData.user)
    setSelectedQuestion(null)
    setEditorValue('')
    setExecutionResult(null)
    setActiveExecutionId(null)
    setPauseRequested(false)
    setExecutionPaused(false)
    setNoticeType('success')
    setNotice(`Logged in as ${loginData.user.email}.`)
  } catch (error) {
    setNoticeType('error')
    setNotice(
      error instanceof Error ? error.message : 'Unable to log in.',
    )
  } finally {
    setConnecting(false)
  }
}


  async function disconnectFromPlatform() {
  if (executionLocked || !connectedKey) return

  setConnecting(true)
  setNotice('')

  try {
    const response = await fetch('/api/auth/logout', {
      method: 'POST',
      headers: { Authorization: `Bearer ${connectedKey}` },
    })

    if (!response.ok) {
      throw new Error(await responseError(response))
    }

    setConnectedKey(null)
    setConnectedUser(null)
    setQuestions([])
    setSelectedQuestion(null)
    setEditorValue('')
    setExecutionResult(null)
    setActiveExecutionId(null)
    setPauseRequested(false)
    setExecutionPaused(false)
    setNoticeType('success')
    setNotice('Logged out successfully.')
  } catch (error) {
    setNoticeType('error')
    setNotice(
      error instanceof Error ? error.message : 'Unable to log out.',
    )
  } finally {
    setConnecting(false)
  }
}


  async function submitSolution() {
    if (!connectedKey || !selectedQuestion || executionLocked) return

    if (!selectedQuestion.permissions_satisfied) {
      setNoticeType('error')
      setNotice('This question requires tool permissions that are not available.')
      return
    }

    const payload: SubmissionPayload = {}

    if (selectedQuestion.type === 'python') {
      payload.source = editorValue
    } else {
      try {
        const parsed: unknown = JSON.parse(editorValue)

        if (
          typeof parsed !== 'object' ||
          parsed === null ||
          Array.isArray(parsed)
        ) {
          throw new Error('The workflow must be a JSON object.')
        }

        payload.specification = parsed as Record<string, unknown>
        payload.execution_id = crypto.randomUUID()
      } catch (error) {
        setNoticeType('error')
        setNotice(
          error instanceof Error ? error.message : 'Invalid workflow JSON.',
        )
        return
      }
    }

    const executionId = payload.execution_id ?? null

    setSubmitting(true)
    setExecutionResult(null)
    setPauseRequested(false)
    setExecutionPaused(false)
    setActiveExecutionId(executionId)
    setNotice('')

    try {
      const response = await fetch(
        `/api/questions/${selectedQuestion.id}/submit`,
        {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${connectedKey}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(payload),
        },
      )

      if (!response.ok) {
        throw new Error(await responseError(response))
      }

      const result = (await response.json()) as ExecutionResponse
      setExecutionResult(result)

      if (result.status === 'paused') {
        setActiveExecutionId(result.execution_id)
        setPauseRequested(true)
        setExecutionPaused(true)
        setNoticeType('success')
        setNotice('Workflow paused at a step boundary. You can resume it.')
      } else {
        setActiveExecutionId(null)
        setPauseRequested(false)
        setExecutionPaused(false)
        setNoticeType(result.status === 'succeeded' ? 'success' : 'error')
        setNotice(
          result.status === 'succeeded'
            ? 'Submission completed.'
            : `Submission status: ${result.status.replaceAll('_', ' ')}`,
        )
      }
    } catch (error) {
      setNoticeType('error')
      setNotice(
        error instanceof Error
          ? `${error.message} The execution state may be unknown if the connection was interrupted.`
          : 'Submission failed. The execution state may be unknown.',
      )

      // Keep the workflow execution ID if the request failed at the network
      // level. The user can still try requesting a pause for this execution.
      if (selectedQuestion.type === 'python') {
        setActiveExecutionId(null)
      }
    } finally {
      setSubmitting(false)
    }
  }

  async function pauseExecution() {
    if (
      !connectedKey ||
      !activeExecutionId ||
      executionPaused ||
      pauseRequested ||
      pausing ||
      resuming
    ) {
      return
    }

    setPausing(true)
    setNotice('')

    try {
      let response: Response | null = null

      // The submit request is synchronous, and its checkpoint may not exist
      // immediately. Retry a 404 briefly before reporting that pause failed.
      for (let attempt = 0; attempt < PAUSE_RETRY_COUNT; attempt += 1) {
        response = await fetch(
          `/api/executions/${activeExecutionId}/pause`,
          {
            method: 'POST',
            headers: { Authorization: `Bearer ${connectedKey}` },
          },
        )

        if (response.status !== 404) {
          break
        }

        if (attempt < PAUSE_RETRY_COUNT - 1) {
          await sleep(PAUSE_RETRY_DELAY_MS)
        }
      }

      if (!response || !response.ok) {
        throw new Error(
          response
            ? await responseError(response)
            : 'Unable to request a pause.',
        )
      }

      setPauseRequested(true)
      setNoticeType('success')
      setNotice(
        'Pause requested. The current workflow step will finish before it pauses.',
      )
    } catch (error) {
      setNoticeType('error')
      setNotice(
        error instanceof Error ? error.message : 'Unable to request a pause.',
      )
    } finally {
      setPausing(false)
    }
  }

  async function resumeExecution() {
    if (
      !connectedKey ||
      !activeExecutionId ||
      !executionPaused ||
      resuming ||
      submitting
    ) {
      return
    }

    setResuming(true)
    setNotice('')

    try {
      const response = await fetch(
        `/api/executions/${activeExecutionId}/resume`,
        {
          method: 'POST',
          headers: { Authorization: `Bearer ${connectedKey}` },
        },
      )

      if (!response.ok) {
        throw new Error(await responseError(response))
      }

      const result = (await response.json()) as ExecutionResponse
      setExecutionResult(result)

      if (result.status === 'paused') {
        setExecutionPaused(true)
        setPauseRequested(true)
        setNoticeType('success')
        setNotice('Workflow is paused. You can resume it again.')
      } else {
        setActiveExecutionId(null)
        setExecutionPaused(false)
        setPauseRequested(false)
        setNoticeType(result.status === 'succeeded' ? 'success' : 'error')
        setNotice(
          result.status === 'succeeded'
            ? 'Workflow resumed and completed successfully.'
            : `Workflow status: ${result.status.replaceAll('_', ' ')}`,
        )
      }
    } catch (error) {
      // Keep the paused state and execution ID so the user can retry.
      setNoticeType('error')
      setNotice(error instanceof Error ? error.message : 'Unable to resume workflow.')
    } finally {
      setResuming(false)
    }
  }

  function resetEditor() {
    if (!selectedQuestion || executionLocked) return

    setEditorValue(
      selectedQuestion.type === 'python'
        ? selectedQuestion.starter_code ?? ''
        : JSON.stringify(selectedQuestion.starter_workflow ?? {}, null, 2),
    )
    setExecutionResult(null)
    setNotice('')
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <h1>Secure Multi-Tenant Agentic Workflow Sandbox</h1>
        <div className="topbar-actions">
          <span className={`connection-status ${isConnected ? 'online' : ''}`}>
            <span className="status-dot" />
            {isConnected ? 'Connected' : 'Not connected'}
          </span>
          {isConnected && (
            <button
              className="disconnect-button"
              type="button"
              onClick={disconnectFromPlatform}
              disabled={executionLocked}
              title={executionLocked ? 'Finish or resume the active execution first' : 'Disconnect'}
            >
              <LogOut size={15} />
              Disconnect
            </button>
          )}
        </div>
      </header>

      <aside className="sidebar">
        <div className="sidebar-title">Questions</div>
        <nav className="question-navigation" aria-label="Questions">
          {questions.map((question) => {
            const isSelected = selectedQuestion?.id === question.id

            return (
              <button
                key={question.id}
                type="button"
                className={`question-nav-item ${isSelected ? 'selected' : ''}`}
                onClick={() => void selectQuestion(question)}
                disabled={loadingQuestion || executionLocked}
              >
                <span className={`question-nav-icon ${question.type}`}>
                  {question.type === 'python' ? (
                    <Code2 size={17} />
                  ) : (
                    <Workflow size={17} />
                  )}
                </span>
                <span className="question-nav-text">
                  <strong>{question.title}</strong>
                  <small>{question.type === 'python' ? 'Python' : 'Workflow'}</small>
                </span>
                {isSelected && <span className="selected-indicator" />}
              </button>
            )
          })}
          {isConnected && questions.length === 0 && (
            <div className="sidebar-empty">No questions available.</div>
          )}
          {!isConnected && (
            <div className="sidebar-empty">Connect to load questions.</div>
          )}
        </nav>

      <div className="sidebar-connection">
  {!isConnected ? (
    <form
      className="connection-form"
      onSubmit={(event) => {
        event.preventDefault()
        void connectToPlatform()
      }}
    >
      <label htmlFor="demo-user">
        <KeyRound size={14} /> Demo account
      </label>

      <select
        id="demo-user"
        value={selectedDemoEmail}
        onChange={(event) => setSelectedDemoEmail(event.target.value)}
        disabled={demoUsersLoading || connecting || demoUsers.length === 0}
      >
        {demoUsers.length === 0 && (
          <option value="">
            {demoUsersLoading ? 'Loading users…' : 'No demo users available'}
          </option>
        )}

        {demoUsers.map((user) => (
          <option key={user.user_id} value={user.email}>
            {user.email}
          </option>
        ))}
      </select>

      <button
        className="connect-button"
        type="submit"
        disabled={connecting || demoUsersLoading || !selectedDemoEmail}
      >
        {connecting ? (
          <LoaderCircle size={16} className="spin" />
        ) : (
          <KeyRound size={15} />
        )}
        {connecting ? 'Logging in…' : 'Login'}
      </button>
    </form>
  ) : (
    <div className="connected-account">
      <span className="connected-account-dot" />
      <div className="connected-account-details">
        <strong>{connectedUser?.email}</strong>
        <small>Tenant: {connectedUser?.tenant_id}</small>
      </div>
    </div>
  )}
</div>
      </aside>

      <main className="main-area">
        <div className="page-content">
          {notice && (
            <div className={`notice ${noticeType}`} role="status">
              {noticeType === 'success' ? (
                <CheckCircle2 size={17} />
              ) : (
                <CircleAlert size={17} />
              )}
              <span>{notice}</span>
              <button
                type="button"
                aria-label="Dismiss message"
                onClick={() => setNotice('')}
              >
                ×
              </button>
            </div>
          )}

          {!isConnected ? (
            <section className="empty-state">
              <div className="empty-state-icon">
                <KeyRound size={28} />
              </div>
              <h2>Connect to continue</h2>
            </section>
          ) : loadingQuestion && !selectedQuestion ? (
            <section className="empty-state">
              <LoaderCircle size={28} className="spin loading-icon" />
              <h2>Loading question…</h2>
            </section>
          ) : !selectedQuestion ? (
            <section className="empty-state">
              <div className="empty-state-icon">
                <FileCode2 size={28} />
              </div>
              <h2>Select a question</h2>
              <p>Choose a question from the sidebar.</p>
            </section>
          ) : (
            <div className="question-page">
              <div className="question-page-heading">
                <div>
                  <div className="question-id">{selectedQuestion.id}</div>
                  <h2>{selectedQuestion.title}</h2>
                </div>
                <span className={`type-pill ${selectedQuestion.type}`}>
                  {selectedQuestion.type === 'python' ? 'Python' : 'Workflow'}
                </span>
              </div>

              <div className="question-overview">
                <section className="content-card problem-card">
                  <h3>Problem statement</h3>
                  <p className="problem-text">{selectedQuestion.statement}</p>
                  {selectedQuestion.requirements.length > 0 && (
                    <div className="requirements-block">
                      <h4>Requirements</h4>
                      <ul className="requirements-list">
                        {selectedQuestion.requirements.map((requirement) => (
                          <li key={requirement}>
                            <CheckCircle2 size={16} />
                            <span>{requirement}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </section>

                <section className="content-card policy-card">
                  <h3>Execution policy</h3>
                  <div className="policy-rows">
                    <div className="policy-row">
                      <span><Cpu size={16} /> CPU</span>
                      <strong>{selectedQuestion.resource_limits.cpu}</strong>
                    </div>
                    <div className="policy-row">
                      <span><Database size={16} /> Memory</span>
                      <strong>{selectedQuestion.resource_limits.memory}</strong>
                    </div>
                    <div className="policy-row">
                      <span><Clock3 size={16} /> Time limit</span>
                      <strong>{selectedQuestion.resource_limits.timeout_seconds}s</strong>
                    </div>
                    <div className="policy-row">
                      <span><Terminal size={16} /> Output limit</span>
                      <strong>{formatBytes(selectedQuestion.resource_limits.max_output_bytes)}</strong>
                    </div>
                  </div>

                  <div className="policy-tools">
                    <h4>Permitted tools</h4>
                    {selectedQuestion.allowed_tools.length > 0 ? (
                      <div className="tool-list">
                        {selectedQuestion.allowed_tools.map((tool) => {
                          const available = selectedQuestion.available_tools.includes(tool)
                          return (
                            <span className={`tool-chip ${available ? 'available' : 'unavailable'}`} key={tool}>
                              {available ? <CheckCircle2 size={14} /> : <CircleAlert size={14} />}
                              {tool}
                            </span>
                          )
                        })}
                      </div>
                    ) : (
                      <p className="no-tools">No tools required.</p>
                    )}
                  </div>

                  {!selectedQuestion.permissions_satisfied && (
                    <div className="permission-warning">
                      <CircleAlert size={16} />
                      Required tool permissions are unavailable.
                    </div>
                  )}
                </section>
              </div>

              <div className="workbench-grid">
                <section className="content-card editor-card">
                  <div className="card-heading editor-card-heading">
                    <div>
                      <h3>Your submission</h3>
                      <div className="editor-filename">
                        {selectedQuestion.type === 'python' ? (
                          <FileCode2 size={15} />
                        ) : (
                          <Braces size={15} />
                        )}
                        {selectedQuestion.type === 'python' ? 'solution.py' : 'workflow.json'}
                      </div>
                    </div>
                    <button
                      className="reset-button"
                      type="button"
                      onClick={resetEditor}
                      disabled={executionLocked}
                    >
                      <RotateCcw size={15} />
                      Reset to starter
                    </button>
                  </div>

                  <textarea
                    className="code-editor"
                    aria-label={selectedQuestion.type === 'python' ? 'Python code editor' : 'Workflow JSON editor'}
                    value={editorValue}
                    onChange={(event) => setEditorValue(event.target.value)}
                    disabled={executionLocked}
                    spellCheck={false}
                    autoCapitalize="off"
                    autoComplete="off"
                    autoCorrect="off"
                  />

                  <div className="editor-footer">
                    {selectedQuestion.type === 'workflow' && (
                      <span className="editor-format">JSON</span>
                    )}

                    <div className="execution-controls">
                      {selectedQuestion.type === 'workflow' &&
                        activeExecutionId &&
                        !executionPaused && (
                          <button
                            className="pause-button"
                            type="button"
                            onClick={() => void pauseExecution()}
                            disabled={pausing || pauseRequested || resuming}
                          >
                            {pausing ? (
                              <LoaderCircle size={16} className="spin" />
                            ) : (
                              <Pause size={16} />
                            )}
                            {pausing
                              ? 'Requesting…'
                              : pauseRequested
                                ? 'Pause requested'
                                : 'Pause workflow'}
                          </button>
                        )}

                      {selectedQuestion.type === 'workflow' && executionPaused && (
                        <button
                          className="resume-button"
                          type="button"
                          onClick={() => void resumeExecution()}
                          disabled={resuming}
                        >
                          {resuming ? (
                            <LoaderCircle size={16} className="spin" />
                          ) : (
                            <Play size={16} fill="currentColor" />
                          )}
                          {resuming ? 'Resuming…' : 'Resume workflow'}
                        </button>
                      )}

                      {!executionLocked && (
                        <button
                          className="submit-button"
                          type="button"
                          disabled={
                            submitting ||
                            !selectedQuestion.permissions_satisfied ||
                            !editorValue.trim()
                          }
                          onClick={() => void submitSolution()}
                        >
                          {submitting ? (
                            <LoaderCircle size={17} className="spin" />
                          ) : (
                            <Play size={16} fill="currentColor" />
                          )}
                          {submitting ? 'Submitting…' : 'Submit solution'}
                        </button>
                      )}
                    </div>
                  </div>

                  {selectedQuestion.type === 'workflow' && activeExecutionId && (
                    <div className={`execution-state ${executionPaused ? 'paused' : ''}`}>
                      <span className="execution-state-dot" />
                      {executionPaused
                        ? 'Workflow is paused. Its checkpoint is ready to resume.'
                        : pauseRequested
                          ? 'Pause requested. Waiting for the current step to finish…'
                          : submitting
                            ? 'Workflow is running.'
                            : 'Execution state may be unknown. You can request a pause if it is still running.'}
                    </div>
                  )}
                </section>

                <section className="content-card results-card">
                  <div className="card-heading">
                    <h3>Results</h3>
                    <Terminal size={19} className="heading-icon" />
                  </div>

                  {!executionResult ? (
                    <div className="results-empty">
                      <Terminal size={25} />
                      <p>No submission yet.</p>
                    </div>
                  ) : (
                    <div className="results-content">
                      <div className="result-status">
                        <span className={`result-status-icon ${executionResult.status}`}>
                          {executionResult.status === 'succeeded' ? (
                            <CheckCircle2 size={17} />
                          ) : (
                            <CircleAlert size={17} />
                          )}
                        </span>
                        <div>
                          <strong>{executionResult.status.replaceAll('_', ' ')}</strong>
                          <small>{executionResult.submission_type} submission</small>
                        </div>
                      </div>

                      {executionResult.execution_result && (
                        <>
                          <div className="output-block">
                            <h4>Standard output</h4>
                            <pre>{executionResult.execution_result.stdout || 'No output'}</pre>
                          </div>

                          {executionResult.execution_result.stderr && (
                            <div className="output-block">
                              <h4 className="stderr-heading">Standard error</h4>
                              <pre className="stderr-output">{executionResult.execution_result.stderr}</pre>
                            </div>
                          )}

                          {executionResult.execution_result.output && (
                            <div className="output-block">
                              <h4>Structured output</h4>
                              <pre>{JSON.stringify(executionResult.execution_result.output, null, 2)}</pre>
                            </div>
                          )}

                          <div className="result-metadata">
                            <span>Exit code: {executionResult.execution_result.exit_code ?? '—'}</span>
                            <span>{executionResult.execution_result.duration_seconds.toFixed(3)}s</span>
                          </div>

                          {(executionResult.execution_result.timed_out ||
                            executionResult.execution_result.oom_killed ||
                            executionResult.execution_result.output_truncated) && (
                            <div className="result-flags">
                              {executionResult.execution_result.timed_out && <span>Time limit reached</span>}
                              {executionResult.execution_result.oom_killed && <span>Memory limit reached</span>}
                              {executionResult.execution_result.output_truncated && <span>Output truncated</span>}
                            </div>
                          )}
                        </>
                      )}

                      {executionResult.step_results && (
                        <div className="workflow-results">
                          <h4>Workflow steps</h4>
                          {Object.entries(executionResult.step_results).map(([stepId, stepResult]) => (
                            <details className="step-result" key={stepId}>
                              <summary>
                                <span><CheckCircle2 size={15} /> {stepId}</span>
                              </summary>
                              <pre>{JSON.stringify(stepResult, null, 2)}</pre>
                            </details>
                          ))}
                        </div>
                      )}

                      {executionResult.tools_used && executionResult.tools_used.length > 0 && (
                        <div className="invoked-tools">
                          <h4>Tools used</h4>
                          <div className="tool-list">
                            {executionResult.tools_used.map((tool) => (
                              <span className="tool-chip available" key={tool}>{tool}</span>
                            ))}
                          </div>
                        </div>
                      )}

                      <div className="execution-reference">
                        <span>Execution ID</span>
                        <code>{executionResult.execution_id}</code>
                      </div>
                    </div>
                  )}
                </section>
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  )
}

export default App
