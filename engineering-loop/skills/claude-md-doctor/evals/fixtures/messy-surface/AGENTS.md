# Plotbot - AI Agent Instructions

This repository is the home for everything about the plotbot pen plotter: the
machine itself, the controller host that drives it, and the pen/paper profiles
that feed it. This file provides critical context for AI agents working here.

## Scope — what lives here, what lives in shop-services

This project owns the **plotter**: the machine, its firmware config, the
controller stack, and the drawing profiles.

`../shop-services` is the workshop IT repo. It owns the controller host as a
network citizen — its DHCP lease and SSH access path — and nothing about
plotting. Tickets get filed there, since this repo has no remote.

The old label printer used to squat on the controller host and should not come
back to it — that's shop-services' problem (their issue #41), not ours.

## ⚠️ IMPORTANT: Custom Configuration

**This is a heavily modified plotter, NOT a stock AxiDraw clone.**

### Deviations from Stock

1. **Single Pen Carriage Only**
   - Only the left carriage position is installed and active
   - The tool-changer hardware was removed
   - Multi-pen G-code sequences are not applicable

2. **Custom Serial Protocol**
   - The controller speaks a custom line protocol, NOT stock GRBL
   - Stock GRBL commands will be silently dropped

### Why These Changes

- The tool changer was unused, complicated the firmware, and added maintenance
  burden, so we removed it back when the firmware was rewritten.
- The custom protocol predates GRBL support on this board.

### Implications for AI Agents

**DO NOT:**
- Suggest reverting to stock AxiDraw or GRBL configuration
- Assume multi-pen capability
- Send stock GRBL commands to the controller

## Configuration Architecture

Profiles use a two-tier system:

### 1. Machine Profile (Hardware Layer)

**What**: Defines hardware capabilities and physical constraints
**Contains**: pen-lift servo range, travel limits, max speeds

**Current Setup**:
- Name: "plotbot-main"
- Connects to: `plotbot.local` over the custom serial-over-TCP bridge
- Travel: 300mm x 218mm

**Location**: `profiles/machine/*.ini`

### 2. Drawing Profile (Job Layer)

**What**: Per-job speed/quality trade-offs
**Contains**: pen-down speed, acceleration, passes

**Current Setup**:
- **draft-fast**: high speed, single pass — the default
- Currently selected profile is stored in `profiles/state.ini`

**Location**: `profiles/*.ini`

## File Structure

```
plotbot/
├── profiles/                    # Drawing + machine profiles
│   ├── fast-draft.ini           # High-speed single-pass profile
│   └── machine/
│       └── plotbot-main.ini     # Machine limits and servo range
├── docs/
│   ├── host.md                  # The controller host
│   └── tips.md                  # Profile-editing lessons learned
├── AGENTS.md                    # This file
└── CLAUDE.md                    # Reference to AGENTS.md
```

## Making Changes Safely

1. **Close the plot server** — it must not be running when editing profiles
2. Edit the `.ini` files in this repo
3. Restart the server and verify settings load
4. Commit to git

**Important**: The plot server overwrites profile files on shutdown. Always
stop the server before manually editing, then restart to test.

## Common Scenarios

### Swapping Pens

1. Physically swap the pen in the carriage
2. Re-run the servo calibration: `plotctl calibrate --servo`
3. Update `pen_width_mm` in the active drawing profile
4. Plot the calibration cross before any real job

### Creating a New Drawing Profile

1. Copy an existing profile in `profiles/`
2. Rename it for the use case (e.g. `fine-multi.ini`)
3. Adjust `pen_down_speed`, `passes`
4. Keep `servo_range` untouched — it belongs to the machine profile

## Git Workflow

This repo is version-controlled for tracking configuration changes over time.

```bash
# Check what changed
git status
git diff

# Commit changes
git add .
git commit -m "Descriptive message about what changed and why"

# View history
git log --oneline
```

## Troubleshooting

**Server doesn't see profiles after editing**:
- Ensure you stopped the server before editing
- Check file permissions (should be readable)
- Verify .ini syntax is valid

**Changes get overwritten**:
- The plot server writes profiles on shutdown
- Always edit with the server stopped

## References

- [Custom protocol notes](docs/host.md)
- [Profile editing tips](docs/tips.md)
