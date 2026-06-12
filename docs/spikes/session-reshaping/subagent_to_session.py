import json, uuid, sys
# subagent transcript -> top-level resumable session.
# argv1 = agentId to convert ; argv2 = optional target session guid (default: mint fresh)
D="/Users/coryking/.claude/projects/-Users-coryking-projects-coryking-plugins"
PARENT="0422be7b-ccd0-40d9-b5db-00f4cca4127b"
AGENT=sys.argv[1]
NEW = sys.argv[2] if len(sys.argv)>2 else str(uuid.uuid4())

src=f"{D}/{PARENT}/subagents/agent-{AGENT}.jsonl"
STRIP=("agentId","attributionAgent","attributionSkill","sourceToolAssistantUUID")
chain=[]; parent=None
for l in open(src):
    d=json.loads(l)
    if d.get("type") not in ("user","assistant"): continue
    for k in STRIP: d.pop(k,None)
    d["isSidechain"]=False
    d["sessionId"]=NEW
    d["parentUuid"]=parent
    u=d.get("uuid") or str(uuid.uuid4()); d["uuid"]=u; parent=u
    chain.append(d)

header=[{"type":"mode","mode":"normal","sessionId":NEW},
        {"type":"permission-mode","permissionMode":"default","sessionId":NEW},
        {"type":"custom-title","customTitle":"branch-b-reconstituted","sessionId":NEW}]
out=f"{D}/{NEW}.jsonl"
import os; existed=os.path.exists(out)
with open(out,"w") as f:
    for ln in header+chain: f.write(json.dumps(ln)+"\n")
print("TARGET_GUID:",NEW,"(overwrote existing)" if existed else "(new file)")
print("turns:",len(chain),"| first isSidechain:",chain[0]["isSidechain"],"| agentId present:", "agentId" in chain[0])
