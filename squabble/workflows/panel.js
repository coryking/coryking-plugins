export const meta = {
  name: 'squabble-panel',
  description: 'A panel of expert reviewers argues over a design: independent reviews, bounded rebuttal rounds until the room goes quiet, one verbatim record',
  phases: [
    { title: 'Review', detail: 'each rostered reviewer reads the brief and artifacts independently' },
    { title: 'Record', detail: 'the recorder assembles the verbatim record' },
  ],
}

// args contract (all strings are markdown unless noted):
//   question    - the human's question, verbatim
//   candidates  - every design option, equal depth, origin-tagged
//   artifacts   - absolute paths of files reviewers must read, plus binding project docs
//   ruled_out   - topics the human has excluded ("none" if empty)
//   context     - facts and constraints that live only in the calling conversation
//   roster      - array of 3-6 role names
//   working_dir - absolute path of the project reviewers work in

const ROLES = ['framer', 'skeptic', 'engineer', 'wildcard', 'auditor', 'calibrator', 'stress-tester', 'historian']
const LIVE_MOVES = ['WITHDRAW', 'UPDATE', 'DISPUTE']
const MAX_REBUTTAL_ROUNDS = 4

const a = args ?? {}
for (const field of ['question', 'candidates', 'artifacts', 'ruled_out', 'context', 'working_dir']) {
  if (typeof a[field] !== 'string' || !a[field].trim()) throw new Error(`args.${field} must be a non-empty string`)
}
if (!Array.isArray(a.roster) || a.roster.length < 3 || a.roster.length > 6) {
  throw new Error('args.roster must be an array of 3-6 role names')
}
const roster = a.roster.map((r) => String(r).toLowerCase().trim())
for (const r of roster) {
  if (!ROLES.includes(r)) throw new Error(`unknown role "${r}" — valid: ${ROLES.join(', ')}`)
}

const brief = `# Squabble brief

## 1. The question
> ${a.question}

## 2. The candidates
${a.candidates}

## 3. The artifacts
Reviewers must open and read these before arguing.
${a.artifacts}

## 4. Context
${a.context}

### Ruled out
The human has excluded these topics. They are settled. Do not raise them.
${a.ruled_out}`

const reviewPrompt = (role) => `You are the ${role} on a Squabble design panel.

The brief is below. Its §1 question bounds your report; its §4 ruled-out list is settled and off-limits. Work from ${a.working_dir} (absolute paths only), and actually open every artifact in §3 — a claim about what a file contains that you did not check against the file is worth nothing.

How to argue:
- Steelman first: state the strongest version of what you're attacking, then attack that.
- Locate and make falsifiable: quote the specific number, assumption, or structural choice, and say what evidence would prove you wrong.
- Propose, don't just poke: every objection comes with a fix, an alternative, or the question that would resolve it.
- Label each finding VERIFIABLE (you can quote the line) or JUDGMENT (it depends on something not on the table).
- If the design is sound from your angle, say so and stop. Do not manufacture findings.
- Research freely (web, code, data), but never send the project's private data to any external service.

Your report, 700 words max:
1. Direct answer to the §1 question, first.
2. Findings, ranked by what would change the decision.
3. If you believe the question itself is wrong: one section labeled "Premise challenge" proposing the question you'd ask instead — a replacement, never an addition.

Plain language throughout; define any term of art when you first use it.

--- BRIEF ---

${brief}`

const REBUTTAL_SCHEMA = {
  type: 'object',
  required: ['moves', 'rebuttal'],
  additionalProperties: false,
  properties: {
    moves: {
      type: 'array',
      minItems: 1,
      description: 'Census of your moves this round: one entry per move, or a single NONE entry if nothing moved you.',
      items: {
        type: 'object',
        required: ['type', 'target'],
        additionalProperties: false,
        properties: {
          type: { enum: ['WITHDRAW', 'UPDATE', 'DISPUTE', 'SECOND', 'NONE'] },
          target: { type: 'string', description: 'Whose claim, e.g. "own finding 3" or "auditor finding 2". Empty string for NONE.' },
        },
      },
    },
    rebuttal: {
      type: 'string',
      description: 'Your rebuttal prose exactly as peers and the record will see it. "No changes" if nothing moved you.',
    },
  },
}

const rebuttalPrompt = (role, ownHistory, peersLatest) => `You are the ${role} on a Squabble design panel, in a rebuttal round.

Below are the brief, your own contributions so far, and your peers' latest contributions. Give your rebuttal, 400 words max. Allowed moves, and nothing else:
- WITHDRAW or UPDATE a claim of yours a peer's evidence undermined — say what changed your mind.
- DISPUTE a peer claim that is wrong, with the evidence. Re-check the artifact if needed; work from ${a.working_dir}.
- SECOND a peer claim your angle independently confirms, and say what your angle adds.

No new findings. No new topics. "No changes" is a complete, useful answer.

--- BRIEF ---

${brief}

--- YOUR CONTRIBUTIONS SO FAR ---

${ownHistory}

--- YOUR PEERS, LATEST ROUND ---

${peersLatest}`

phase('Review')
log(`Review: ${roster.join(', ')} working independently`)
const pass1 = await parallel(
  roster.map((role) => () => agent(reviewPrompt(role), { agentType: `squabble:${role}`, label: `review:${role}`, phase: 'Review' })),
)

const own = {} // role -> array of that role's contributions, in order
const latest = {} // role -> most recent contribution text
const transcript = [] // { role, round, text }
const lostSeats = []
roster.forEach((role, i) => {
  if (typeof pass1[i] === 'string' && pass1[i].trim()) {
    own[role] = [pass1[i]]
    latest[role] = pass1[i]
    transcript.push({ role, round: 'review', text: pass1[i] })
  } else {
    lostSeats.push(`${role} (review round)`)
  }
})
const active = roster.filter((r) => own[r])
if (active.length < 2) throw new Error(`only ${active.length} reviewer(s) returned a report — not enough for a rebuttal round`)

let round = 0
let liveMotion = true
while (liveMotion && round < MAX_REBUTTAL_ROUNDS) {
  round++
  phase(`Rebuttal ${round}`)
  const results = await parallel(
    active.map((role) => () => {
      const peers = active
        .filter((r) => r !== role)
        .map((r) => `## ${r.toUpperCase()}\n\n${latest[r]}`)
        .join('\n\n---\n\n')
      const history = own[role].join('\n\n--- (your next contribution) ---\n\n')
      return agent(rebuttalPrompt(role, history, peers), {
        agentType: `squabble:${role}`,
        label: `rebuttal${round}:${role}`,
        phase: `Rebuttal ${round}`,
        schema: REBUTTAL_SCHEMA,
      })
    }),
  )
  let liveCount = 0
  active.forEach((role, i) => {
    const r = results[i]
    if (!r) {
      transcript.push({ role, round: `rebuttal ${round}`, text: '(no response this round)' })
      return
    }
    liveCount += r.moves.filter((m) => LIVE_MOVES.includes(m.type)).length
    own[role].push(r.rebuttal)
    latest[role] = r.rebuttal
    transcript.push({ role, round: `rebuttal ${round}`, text: r.rebuttal })
  })
  liveMotion = liveCount > 0
  log(`Rebuttal ${round}: ${liveCount} live move(s) — ${liveMotion ? 'running another round' : 'the room is quiet'}`)
}

const ended = liveMotion
  ? `capped after rebuttal round ${MAX_REBUTTAL_ROUNDS} with live motion still on the table — remaining disputes are open, not settled`
  : `quiet after rebuttal round ${round} — a full round produced no withdrawals, updates, or disputes`

phase('Record')
log('Recorder assembling the record')
const corpus = transcript.map((t) => `## ${t.role.toUpperCase()} — ${t.round}\n\n${t.text}`).join('\n\n---\n\n')
const record = await agent(
  `A squabble has completed. Assemble its record.

The human's question, verbatim:
> ${a.question}

Ruled-out topics (findings that strayed here go in the out-of-scope bin):
${a.ruled_out}

How it ended: ${ended}${lostSeats.length ? `. Seats lost to errors: ${lostSeats.join(', ')}.` : ''}

Every contribution is below, verbatim, labeled by role and round. Write the record.

--- CORPUS ---

${corpus}`,
  { agentType: 'squabble:recorder', label: 'recorder', phase: 'Record' },
)

return record
