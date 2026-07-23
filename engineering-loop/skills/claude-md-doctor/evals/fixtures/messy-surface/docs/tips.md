# Profile-editing lessons learned

Hard-won lessons about hand-editing the `.ini` profiles.

## Common Issues & Solutions

**Server doesn't see profiles after editing**:
- Ensure you stopped the server before editing
- Check file permissions (should be readable)
- Verify .ini syntax is valid

**Changes get overwritten**:
- The plot server writes profiles on shutdown
- Always edit with the server stopped

## Editing rules

- One profile per file; the filename is the profile name.
- `servo_range` lives only in the machine profile — never copy it into a
  drawing profile, the server rejects the duplicate key.
