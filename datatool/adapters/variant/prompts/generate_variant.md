You are generating a single UI variant for an A/B test. The controller will safely
ramp, evaluate, and either promote or revert this variant within a trust contract —
your job is only to produce the candidate.

## Surface description

{{SURFACE_DESCRIPTION}}

## Component to change

Path: {{COMPONENT_PATH}}

### Current implementation

{{CURRENT_IMPLEMENTATION}}

## Hard constraints

You MUST NOT reference, import, or render any of these forbidden components. Producing
output that mentions any of them is a failure:

{{FORBIDDEN_COMPONENTS}}

## Additional instructions

{{EXTRA_INSTRUCTIONS}}

## Output format

Return ONLY a single JSON object — no prose before or after — with exactly these
fields:

- `summary`: a one-line description of the change.
- `rationale`: why this variant might move the goal metric.
- `code`: the full code for the new variant of the component.

Example shape (do not copy the contents, only the structure):

{"summary": "...", "rationale": "...", "code": "..."}
