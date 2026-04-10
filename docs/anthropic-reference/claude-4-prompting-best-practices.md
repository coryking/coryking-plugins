# Prompting best practices

> Source: https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/claude-4-best-practices
>
> Single reference for prompt engineering with Claude Opus 4.6, Sonnet 4.6, and Haiku 4.5.

## General principles

### Be clear and direct

Claude responds well to clear, explicit instructions. Being specific about your desired output can help enhance results. If you want "above and beyond" behavior, explicitly request it rather than relying on the model to infer this from vague prompts.

Think of Claude as a brilliant but new employee who lacks context on your norms and workflows. The more precisely you explain what you want, the better the result.

**Golden rule:** Show your prompt to a colleague with minimal context on the task and ask them to follow it. If they'd be confused, Claude will be too.

- Be specific about the desired output format and constraints.
- Provide instructions as sequential steps using numbered lists or bullet points when the order or completeness of steps matters.

**Less effective:**
```
Create an analytics dashboard
```

**More effective:**
```
Create an analytics dashboard. Include as many relevant features and interactions as possible. Go beyond the basics to create a fully-featured implementation.
```

### Add context to improve performance

Providing context or motivation behind your instructions, such as explaining to Claude why such behavior is important, can help Claude better understand your goals and deliver more targeted responses.

**Less effective:**
```
NEVER use ellipses
```

**More effective:**
```
Your response will be read aloud by a text-to-speech engine, so never use ellipses since the text-to-speech engine will not know how to pronounce them.
```

Claude is smart enough to generalize from the explanation.

### Use examples effectively

Examples are one of the most reliable ways to steer Claude's output format, tone, and structure. A few well-crafted examples (known as few-shot or multishot prompting) can dramatically improve accuracy and consistency.

When adding examples, make them:
- **Relevant:** Mirror your actual use case closely.
- **Diverse:** Cover edge cases and vary enough that Claude doesn't pick up unintended patterns.
- **Structured:** Wrap examples in `<example>` tags (multiple examples in `<examples>` tags) so Claude can distinguish them from instructions.

Include 3-5 examples for best results. You can also ask Claude to evaluate your examples for relevance and diversity, or to generate additional ones based on your initial set.

### Structure prompts with XML tags

XML tags help Claude parse complex prompts unambiguously, especially when your prompt mixes instructions, context, examples, and variable inputs. Wrapping each type of content in its own tag (e.g. `<instructions>`, `<context>`, `<input>`) reduces misinterpretation.

Best practices:
- Use consistent, descriptive tag names across your prompts.
- Nest tags when content has a natural hierarchy (documents inside `<documents>`, each inside `<document index="n">`).

### Give Claude a role

Setting a role in the system prompt focuses Claude's behavior and tone for your use case. Even a single sentence makes a difference.

### Long context prompting

When working with large documents or data-rich inputs (20k+ tokens):

- **Put longform data at the top**: Place your long documents and inputs near the top of your prompt, above your query, instructions, and examples. Queries at the end can improve response quality by up to 30% in tests, especially with complex, multi-document inputs.

- **Structure document content and metadata with XML tags**: When using multiple documents, wrap each document in `<document>` tags with `<document_content>` and `<source>` subtags for clarity.

- **Ground responses in quotes**: For long document tasks, ask Claude to quote relevant parts of the documents first before carrying out its task. This helps Claude cut through the noise of the rest of the document's contents.

### Model self-knowledge

If you would like Claude to identify itself correctly in your application:

```
The assistant is Claude, created by Anthropic. The current model is Claude Opus 4.6.
```

For apps that need to specify model strings:

```
When an LLM is needed, please default to Claude Opus 4.6 unless the user requests otherwise. The exact model string for Claude Opus 4.6 is claude-opus-4-6.
```

## Output and formatting

### Communication style and verbosity

Claude's latest models have a more concise and natural communication style compared to previous models:

- **More direct and grounded:** Provides fact-based progress reports rather than self-celebratory updates
- **More conversational:** Slightly more fluent and colloquial, less machine-like
- **Less verbose:** May skip detailed summaries for efficiency unless prompted otherwise

This means Claude may skip verbal summaries after tool calls, jumping directly to the next action. If you prefer more visibility into its reasoning:

```
After completing a task that involves tool use, provide a quick summary of the work you've done.
```

### Control the format of responses

1. **Tell Claude what to do instead of what not to do**
   - Instead of: "Do not use markdown in your response"
   - Try: "Your response should be composed of smoothly flowing prose paragraphs."

2. **Use XML format indicators**
   - Try: "Write the prose sections of your response in \<smoothly_flowing_prose_paragraphs\> tags."

3. **Match your prompt style to the desired output**
   The formatting style used in your prompt may influence Claude's response style. Removing markdown from your prompt can reduce the volume of markdown in the output.

4. **Use detailed prompts for specific formatting preferences**

```
<avoid_excessive_markdown_and_bullet_points>
When writing reports, documents, technical explanations, analyses, or any long-form content, write in clear, flowing prose using complete paragraphs and sentences. Use standard paragraph breaks for organization and reserve markdown primarily for `inline code`, code blocks, and simple headings. Avoid using **bold** and *italics*.

DO NOT use ordered lists or unordered lists unless: a) you're presenting truly discrete items where a list format is the best option, or b) the user explicitly requests a list or ranking

Instead of listing items with bullets or numbers, incorporate them naturally into sentences. Using prose instead of excessive formatting will improve user satisfaction. NEVER output a series of overly short bullet points.

Your goal is readable, flowing text that guides the reader naturally through ideas rather than fragmenting information into isolated points.
</avoid_excessive_markdown_and_bullet_points>
```

### LaTeX output

Claude Opus 4.6 defaults to LaTeX for mathematical expressions. If you prefer plain text:

```
Format your response in plain text only. Do not use LaTeX, MathJax, or any markup notation such as \( \), $, or \frac{}{}. Write all math expressions using standard text characters (e.g., "/" for division, "*" for multiplication, and "^" for exponents).
```

### Migrating away from prefilled responses

Starting with Claude 4.6 models, prefilled responses on the last assistant turn are no longer supported. Common migration patterns:

- **Controlling output formatting**: Use structured outputs, or simply ask the model to conform to your output structure — newer models can reliably match complex schemas when told to.
- **Eliminating preambles**: Use direct instructions in the system prompt: "Respond directly without preamble."
- **Avoiding bad refusals**: Claude is much better at appropriate refusals now. Clear prompting without prefill should be sufficient.
- **Continuations**: Move to the user message: "Your previous response was interrupted and ended with `[previous_response]`. Continue from where you left off."
- **Context hydration**: Inject what were previously prefilled-assistant reminders into the user turn.

## Tool use

### Tool usage

Claude's latest models are trained for precise instruction following and benefit from explicit direction to use specific tools.

**Less effective (Claude will only suggest):**
```
Can you suggest some changes to improve this function?
```

**More effective (Claude will make the changes):**
```
Change this function to improve its performance.
```

To make Claude more proactive about taking action by default:

```
<default_to_action>
By default, implement changes rather than only suggesting them. If the user's intent is unclear, infer the most useful likely action and proceed, using tools to discover any missing details instead of guessing. Try to infer the user's intent about whether a tool call is intended or not, and act accordingly.
</default_to_action>
```

To make Claude more conservative:

```
<do_not_act_before_instructions>
Do not jump into implementation or change files unless clearly instructed to make changes. When the user's intent is ambiguous, default to providing information, doing research, and providing recommendations rather than taking action. Only proceed with edits, modifications, or implementations when the user explicitly requests them.
</do_not_act_before_instructions>
```

**Important:** Opus 4.5 and Opus 4.6 are more responsive to the system prompt than previous models. If your prompts were designed to reduce undertriggering on tools, these models may now overtrigger. Where you might have said "CRITICAL: You MUST use this tool when...", you can use more normal prompting like "Use this tool when...".

### Optimize parallel tool calling

Claude's latest models excel at parallel tool execution:
- Run multiple speculative searches during research
- Read several files at once to build context faster
- Execute bash commands in parallel

You can boost parallel calling to ~100%:

```
<use_parallel_tool_calls>
If you intend to call multiple tools and there are no dependencies between the tool calls, make all of the independent tool calls in parallel. Prioritize calling tools simultaneously whenever the actions can be done in parallel rather than sequentially. Maximize use of parallel tool calls where possible to increase speed and efficiency. However, if some tool calls depend on previous calls to inform dependent values, do NOT call these tools in parallel and instead call them sequentially. Never use placeholders or guess missing parameters in tool calls.
</use_parallel_tool_calls>
```

To reduce parallel execution:
```
Execute operations sequentially with brief pauses between each step to ensure stability.
```

## Thinking and reasoning

### Overthinking and excessive thoroughness

Opus 4.6 does significantly more upfront exploration than previous models, especially at higher effort settings. If your prompts previously encouraged thoroughness, tune that guidance for 4.6:

- **Replace blanket defaults with targeted instructions.** Instead of "Default to using [tool]," use "Use [tool] when it would enhance your understanding of the problem."
- **Remove over-prompting.** Tools that undertriggered in previous models are likely to trigger appropriately now. Instructions like "If in doubt, use [tool]" will cause overtriggering.
- **Use effort as a fallback.** If Claude continues to be overly aggressive, use a lower setting for effort.

To constrain excessive thinking:

```
When you're deciding how to approach a problem, choose an approach and commit to it. Avoid revisiting decisions unless you encounter new information that directly contradicts your reasoning. If you're weighing two approaches, pick one and see it through. You can always course-correct later if the chosen approach fails.
```

### Leverage thinking & interleaved thinking

Claude 4.6 models use adaptive thinking (`thinking: {type: "adaptive"}`), where Claude dynamically decides when and how much to think based on the effort parameter and query complexity. In internal evaluations, adaptive thinking reliably drives better performance than extended thinking.

You can guide Claude's thinking behavior:

```
After receiving tool results, carefully reflect on their quality and determine optimal next steps before proceeding. Use your thinking to plan and iterate based on this new information, and then take the best next action.
```

To reduce thinking frequency:

```
Extended thinking adds latency and should only be used when it will meaningfully improve answer quality - typically for problems that require multi-step reasoning. When in doubt, respond directly.
```

Key tips:
- **Prefer general instructions over prescriptive steps.** "Think thoroughly" often produces better reasoning than a hand-written step-by-step plan.
- **Multishot examples work with thinking.** Use `<thinking>` tags inside few-shot examples to show Claude the reasoning pattern.
- **Ask Claude to self-check.** "Before you finish, verify your answer against [test criteria]." This catches errors reliably, especially for coding and math.

Note: When extended thinking is disabled, Opus 4.5 is particularly sensitive to the word "think" and its variants. Consider using "consider," "evaluate," or "reason through" instead.

## Agentic systems

### Long-horizon reasoning and state tracking

Claude's latest models excel at long-horizon reasoning with exceptional state tracking. Claude maintains orientation across extended sessions by focusing on incremental progress.

#### Context awareness and multi-window workflows

Claude 4.6 and 4.5 models feature context awareness, enabling the model to track its remaining context window throughout a conversation. If using an agent harness that compacts context:

```
Your context window will be automatically compacted as it approaches its limit, allowing you to continue working indefinitely from where you left off. Therefore, do not stop tasks early due to token budget concerns. As you approach your token budget limit, save your current progress and state to memory before the context window refreshes. Always be as persistent and autonomous as possible and complete tasks fully.
```

#### Multi-context window workflows

1. **Use a different prompt for the first context window**: Set up a framework (write tests, create setup scripts), then use future windows to iterate on a todo-list.

2. **Have the model write tests in a structured format**: Ask Claude to create tests before starting work and keep track of them in a structured format (e.g., `tests.json`). Remind Claude: "It is unacceptable to remove or edit tests because this could lead to missing or buggy functionality."

3. **Set up quality of life tools**: Encourage Claude to create setup scripts (e.g., `init.sh`) to gracefully start servers, run test suites, and linters.

4. **Starting fresh vs compacting**: Claude's latest models are extremely effective at discovering state from the local filesystem. Be prescriptive about how it should start:
   - "Call pwd; you can only read and write files in this directory."
   - "Review progress.txt, tests.json, and the git logs."
   - "Manually run through a fundamental integration test before moving on to implementing new features."

5. **Provide verification tools**: As autonomous tasks grow, Claude needs to verify correctness without continuous human feedback.

6. **Encourage complete usage of context**:

```
This is a very long task, so it may be beneficial to plan out your work clearly. It's encouraged to spend your entire output context working on the task - just make sure you don't run out of context with significant uncommitted work. Continue working systematically until you have completed this task.
```

#### State management best practices

- **Use structured formats for state data**: JSON or other structured formats for test results or task status
- **Use unstructured text for progress notes**: Freeform progress notes for tracking general progress
- **Use git for state tracking**: Git provides a log of what's been done and checkpoints that can be restored. Claude's latest models perform especially well in using git to track state.
- **Emphasize incremental progress**: Explicitly ask Claude to keep track of its progress

### Balancing autonomy and safety

Without guidance, Opus 4.6 may take actions that are difficult to reverse or affect shared systems. If you want confirmation before risky actions:

```
Consider the reversibility and potential impact of your actions. You are encouraged to take local, reversible actions like editing files or running tests, but for actions that are hard to reverse, affect shared systems, or could be destructive, ask the user before proceeding.

Examples of actions that warrant confirmation:
- Destructive operations: deleting files or branches, dropping database tables, rm -rf
- Hard to reverse operations: git push --force, git reset --hard, amending published commits
- Operations visible to others: pushing code, commenting on PRs/issues, sending messages, modifying shared infrastructure

When encountering obstacles, do not use destructive actions as a shortcut. For example, don't bypass safety checks (e.g. --no-verify) or discard unfamiliar files that may be in-progress work.
```

### Research and information gathering

For optimal research results:

1. **Provide clear success criteria**: Define what constitutes a successful answer
2. **Encourage source verification**: Ask Claude to verify information across multiple sources
3. **For complex research, use a structured approach**:

```
Search for this information in a structured way. As you gather data, develop several competing hypotheses. Track your confidence levels in your progress notes to improve calibration. Regularly self-critique your approach and plan. Update a hypothesis tree or research notes file to persist information and provide transparency. Break down this complex research task systematically.
```

### Subagent orchestration

Claude's latest models demonstrate significantly improved native subagent orchestration. These models can recognize when tasks benefit from delegating to specialized subagents and do so proactively.

**Watch for overuse**: Opus 4.6 has a strong predilection for subagents and may spawn them when a simpler, direct approach suffices. If you're seeing excessive subagent use:

```
Use subagents when tasks can run in parallel, require isolated context, or involve independent workstreams that don't need to share state. For simple tasks, sequential operations, single-file edits, or tasks where you need to maintain context across steps, work directly rather than delegating.
```

### Reduce file creation in agentic coding

Claude's latest models may create new files for testing and iteration. If you'd prefer to minimize this:

```
If you create any temporary new files, scripts, or helper files for iteration, clean up these files by removing them at the end of the task.
```

### Overeagerness

Opus 4.5 and 4.6 have a tendency to overengineer. To keep solutions minimal:

```
Avoid over-engineering. Only make changes that are directly requested or clearly necessary. Keep solutions simple and focused:

- Scope: Don't add features, refactor code, or make "improvements" beyond what was asked. A bug fix doesn't need surrounding code cleaned up.
- Documentation: Don't add docstrings, comments, or type annotations to code you didn't change.
- Defensive coding: Don't add error handling, fallbacks, or validation for scenarios that can't happen. Trust internal code and framework guarantees. Only validate at system boundaries.
- Abstractions: Don't create helpers, utilities, or abstractions for one-time operations. Don't design for hypothetical future requirements.
```

### Avoid focusing on passing tests and hard-coding

```
Write a high-quality, general-purpose solution using the standard tools available. Do not create helper scripts or workarounds. Implement a solution that works correctly for all valid inputs, not just the test cases. Do not hard-code values or create solutions that only work for specific test inputs.

Focus on understanding the problem requirements and implementing the correct algorithm. Tests are there to verify correctness, not to define the solution.

If the task is unreasonable or infeasible, or if any of the tests are incorrect, please inform me rather than working around them.
```

### Minimizing hallucinations in agentic coding

```
<investigate_before_answering>
Never speculate about code you have not opened. If the user references a specific file, you MUST read the file before answering. Make sure to investigate and read relevant files BEFORE answering questions about the codebase. Never make any claims about code before investigating unless you are certain of the correct answer.
</investigate_before_answering>
```

## Capability-specific tips

### Improved vision capabilities

Opus 4.5 and Opus 4.6 have improved vision capabilities. One technique that boosts performance is giving Claude a crop tool or skill — consistent uplift on image evaluations when Claude can "zoom" in on relevant regions.

### Frontend design

Opus 4.5 and 4.6 excel at building complex web applications but can default to generic "AI slop" aesthetic without guidance:

```
<frontend_aesthetics>
You tend to converge toward generic, "on distribution" outputs. In frontend design, this creates what users call the "AI slop" aesthetic. Avoid this: make creative, distinctive frontends that surprise and delight.

Focus on:
- Typography: Choose fonts that are beautiful, unique, and interesting. Avoid generic fonts like Arial and Inter.
- Color & Theme: Commit to a cohesive aesthetic. Use CSS variables for consistency. Dominant colors with sharp accents outperform timid, evenly-distributed palettes.
- Motion: Use animations for effects and micro-interactions. Focus on high-impact moments: one well-orchestrated page load with staggered reveals creates more delight than scattered micro-interactions.
- Backgrounds: Create atmosphere and depth rather than defaulting to solid colors.

Avoid generic AI-generated aesthetics:
- Overused font families (Inter, Roboto, Arial, system fonts)
- Cliche color schemes (particularly purple gradients on white backgrounds)
- Predictable layouts and component patterns

Vary between light and dark themes, different fonts, different aesthetics. Think outside the box!
</frontend_aesthetics>
```
